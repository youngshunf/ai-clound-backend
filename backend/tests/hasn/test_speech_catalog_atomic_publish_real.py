"""语音内容寻址暂存与原子 release 的真实 PostgreSQL/S3 集成测试。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
import zipfile

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio

from fastapi import UploadFile
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema
from starlette.datastructures import Headers

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.model.hasn_speech_catalog_release import HasnSpeechCatalogRelease
from backend.app.hasn.model.hasn_speech_catalog_release_package import (
    HasnSpeechCatalogReleasePackage,
)
from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage
from backend.app.hasn.schema.hasn_speech_catalog import (
    SpeechCatalogPublishResponse,
    SpeechPackageStageResponse,
)
from backend.app.hasn.service.speech_catalog_service import (
    build_speech_package_object_key,
    compute_revision,
    speech_catalog_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.model.storage import S3Storage
from backend.plugin.s3.schema.storage import UpdateS3StorageParam
from backend.plugin.s3.service.storage import s3_storage_service
from backend.plugin.s3.service.storage_service import StorageService
from backend.plugin.s3.utils.file_ops import get_operator_for_storage

pytestmark = pytest.mark.asyncio
_RUN_MARKER = uuid4().hex


def _require_real_s3_test() -> None:
    """只有显式授权的环境才运行真实对象写入。"""
    if os.getenv('HASN_SPEECH_REAL_S3_TEST') != '1':
        pytest.skip('需显式设置 HASN_SPEECH_REAL_S3_TEST=1 才允许写入并清理真实测试对象')


def _real_zip_bytes(marker: str | None = None) -> bytes:
    """生成确定且可被标准 ZIP 读取器打开的最小模型包。"""
    marker = marker or _RUN_MARKER
    output = io.BytesIO()
    info = zipfile.ZipInfo('manifest.json', date_time=(2026, 7, 19, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(output, mode='w') as archive:
        archive.writestr(info, json.dumps({'schema_version': 1, 'marker': marker}))
    return output.getvalue()


def _upload(package_bytes: bytes) -> UploadFile:
    """把真实字节包装为 FastAPI UploadFile，覆盖生产流式入口。"""
    return UploadFile(
        io.BytesIO(package_bytes),
        filename='integration-speech-package.zip',
        headers=Headers({'content-type': 'application/zip'}),
    )


def _release_document(
    *,
    package_url: str,
    sha256: str,
    compressed_size: int,
    release_sequence: int,
    extra_unstaged_sha256: str | None = None,
) -> str:
    """构造云端结构预检所需的完整 v2 签名信封。"""

    def model(model_id: str, digest: str, url: str) -> dict:
        return {
            'model_id': model_id,
            'display_name': model_id,
            'tier': 'balanced',
            'model_version': '2026.07.19',
            'maturity': 'stable',
            'engine': 'integration',
            'engine_version': '1',
            'quantization': 'int8',
            'languages': ['zh'],
            'capabilities': ['stt'],
            'default_for_languages': ['zh'],
            'fallback_priority': 1,
            'experimental': False,
            'packages': [
                {
                    'platform': {'os': 'macos', 'arch': 'aarch64', 'acceleration': 'cpu'},
                    'url': url,
                    'sha256': digest,
                    'signature': 'ab' * 64,
                    'compressed_size': compressed_size,
                    'installed_size': compressed_size * 2,
                }
            ],
            'minimum_ram_mb': 1,
            'recommended_ram_mb': 1,
            'minimum_free_disk_bytes': compressed_size * 3,
            'license': {
                'name': 'Apache-2.0',
                'url': 'https://www.apache.org/licenses/LICENSE-2.0',
                'source': 'https://github.com/youngshunf/hasn-node',
            },
            'rollout': 100,
            'revoked': False,
            'release_sequence': release_sequence,
            'channel': 'stable',
            'expires_at': '2099-01-01T00:00:00Z',
        }

    models = [model('integration-primary', sha256, package_url)]
    if extra_unstaged_sha256 is not None:
        object_key = build_speech_package_object_key(extra_unstaged_sha256)
        models.append(
            model(
                'integration-missing',
                extra_unstaged_sha256,
                f'https://hasn-pub-cdn.dcfuture.cn/{object_key}',
            )
        )
    document = {
        'payload': {
            'catalog_version': f'integration-{release_sequence}',
            'issued_at': '2026-07-19T00:00:00Z',
            'models': models,
        },
        'key_id': 'speech-prod-2026-07-current',
        'release_sequence': release_sequence,
        'expires_at': '2099-01-01T00:00:00Z',
        'signature': 'cd' * 64,
    }
    return json.dumps(document, separators=(',', ':'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """在独立 PostgreSQL schema 中连接真实 S3，用例结束后回收对象并删除 schema。"""
    _require_real_s3_test()
    base_engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with base_engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await base_engine.dispose()
        pytest.skip(f'真实 PostgreSQL 不可达，跳过: {exc!r}')

    base_maker = async_sessionmaker(base_engine, expire_on_commit=False)
    async with base_maker() as source:
        public_storage = await source.scalar(
            select(S3Storage).where(S3Storage.access == 'public').order_by(S3Storage.id).limit(1)
        )
    if public_storage is None:
        await base_engine.dispose()
        pytest.skip('真实环境未配置 public S3 存储')

    schema = f'speech_it_{uuid4().hex}'
    isolated_engine = base_engine.execution_options(schema_translate_map={None: schema})
    async with isolated_engine.begin() as connection:
        await connection.execute(CreateSchema(schema))
        for table in (
            S3Storage.__table__,
            HasnSpeechPackage.__table__,
            HasnSpeechCatalogRelease.__table__,
            HasnSpeechCatalogReleasePackage.__table__,
            HasnSpeechCatalog.__table__,
        ):
            typed_table = cast('Table', table)
            await connection.run_sync(typed_table.create)
        # codegen 模型不重复声明全部 SQL 约束；隔离 schema 必须补齐权威 DDL，
        # 才能真实覆盖 ON CONFLICT、并发 release 和单 head 语义。
        for statement in (
            f'ALTER TABLE "{schema}"."hasn_speech_package" ALTER COLUMN "created_time" SET DEFAULT now()',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog_release" ALTER COLUMN "created_time" SET DEFAULT now()',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog_release_package" '
            'ALTER COLUMN "created_time" SET DEFAULT now()',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog" ALTER COLUMN "created_time" SET DEFAULT now()',
            f'ALTER TABLE "{schema}"."hasn_speech_package" ADD CONSTRAINT "uq_speech_package_sha256" UNIQUE ("sha256")',
            f'ALTER TABLE "{schema}"."hasn_speech_package" '
            'ADD CONSTRAINT "uq_speech_package_object_key" UNIQUE ("object_key")',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog_release" '
            'ADD CONSTRAINT "uq_speech_catalog_release_revision" UNIQUE ("revision")',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog_release" '
            'ADD CONSTRAINT "uq_speech_catalog_release_sequence" UNIQUE ("release_sequence")',
            f'ALTER TABLE "{schema}"."hasn_speech_catalog_release_package" '
            'ADD CONSTRAINT "uq_speech_release_package_platform" '
            'UNIQUE ("release_id", "model_id", "model_version", "os", "arch", "acceleration")',
        ):
            await connection.execute(text(statement))

    maker = async_sessionmaker(isolated_engine, expire_on_commit=False)
    async with maker.begin() as setup:
        setup.add(
            S3Storage(
                name=f'speech-integration-{schema}',
                endpoint=public_storage.endpoint,
                access_key=public_storage.access_key,
                secret_key=public_storage.secret_key,
                bucket=public_storage.bucket,
                access='public',
                sign_strategy=public_storage.sign_strategy,
                prefix=public_storage.prefix,
                region=public_storage.region,
                cdn_domain=public_storage.cdn_domain,
                remark=public_storage.remark,
            )
        )

    db = maker()
    try:
        yield db
    finally:
        try:
            await db.rollback()
            await db.close()
            async with maker() as cleanup:
                packages = (await cleanup.execute(select(HasnSpeechPackage))).scalars().all()
                for package in packages:
                    storage = await StorageService.get_storage(cleanup, package.storage_id)
                    # 测试 schema 只登记本用例随机摘要；显式绕过生产删除保护回收真实测试对象。
                    await get_operator_for_storage(storage).delete(package.object_key)
        finally:
            async with base_engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await isolated_engine.dispose()
            await base_engine.dispose()


async def test_stage_package_is_real_s3_backed_and_idempotent(session: AsyncSession) -> None:
    package_bytes = _real_zip_bytes()
    expected_sha256 = hashlib.sha256(package_bytes).hexdigest()

    first = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )
    second = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )

    assert first.sha256 == expected_sha256
    assert first.object_key == build_speech_package_object_key(expected_sha256)
    assert first.download_url.startswith('https://')
    assert first.size == len(package_bytes)
    assert first.already_exists is False
    assert second.package_id == first.package_id
    assert second.already_exists is True
    registered = await session.get(HasnSpeechPackage, first.package_id)
    assert registered is not None

    different_bytes = bytearray(package_bytes)
    different_bytes[-1] ^= 0x01
    with pytest.raises(errors.ServerError, match='不可变语音包上传失败'):
        await StorageService.upload_immutable_speech_package(
            session,
            io.BytesIO(bytes(different_bytes)),
            size=len(different_bytes),
            content_type='application/zip',
            key=first.object_key,
        )
    with pytest.raises(errors.RequestError, match='不可变语音包命名空间'):
        await StorageService.upload(
            session,
            b'forbidden-overwrite',
            category='speech_model',
            filename='forbidden.zip',
            content_type='application/zip',
            key=first.object_key,
        )
    actual_sha256, actual_size = await StorageService.sha256(
        session,
        storage_id=registered.storage_id,
        object_key=first.object_key,
    )
    assert actual_sha256 == first.sha256
    assert actual_size == first.size

    storage = await StorageService.get_storage(session, registered.storage_id)
    with pytest.raises(errors.ConflictError, match='禁止修改配置'):
        await s3_storage_service.update(
            db=session,
            pk=storage.id,
            obj=UpdateS3StorageParam(
                name=storage.name,
                endpoint=storage.endpoint,
                access_key=storage.access_key,
                secret_key=storage.secret_key,
                bucket=storage.bucket,
                prefix=storage.prefix,
                region=storage.region,
                cdn_domain=storage.cdn_domain,
                access=storage.access,
                sign_strategy=storage.sign_strategy,
                remark='forbidden-test-change',
            ),
        )


async def test_stage_package_rejects_storage_change_and_retry_recovers_existing_object(
    session: AsyncSession,
) -> None:
    """配置竞态不得写错登记，恢复原配置后必须复用已上传对象完成暂存。"""
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    storage = await session.scalar(select(S3Storage).where(S3Storage.access == 'public').limit(1))
    assert storage is not None
    storage_id = storage.id
    original_prefix = storage.prefix
    original_operator = get_operator_for_storage(storage)
    package_bytes = _real_zip_bytes(f'storage-race-{_RUN_MARKER}')
    digest = hashlib.sha256(package_bytes).hexdigest()
    object_key = build_speech_package_object_key(digest)
    changed_prefix = '/'.join(
        part for part in (original_prefix.strip('/') if original_prefix else '', f'speech-race-{_RUN_MARKER}') if part
    )
    await session.rollback()

    async def restore_storage_prefix() -> None:
        async with maker.begin() as restore:
            await restore.execute(update(S3Storage).where(S3Storage.id == storage_id).values(prefix=original_prefix))

    stage_task: asyncio.Task[SpeechPackageStageResponse] | None = None
    async with maker() as admin:
        await admin.begin()
        admin_storage = await admin.get(S3Storage, storage_id)
        assert admin_storage is not None
        await s3_storage_service.update(
            db=admin,
            pk=storage_id,
            obj=UpdateS3StorageParam(
                name=admin_storage.name,
                endpoint=admin_storage.endpoint,
                access_key=admin_storage.access_key,
                secret_key=admin_storage.secret_key,
                bucket=admin_storage.bucket,
                prefix=changed_prefix,
                region=admin_storage.region,
                cdn_domain=admin_storage.cdn_domain,
                access=admin_storage.access,
                sign_strategy=admin_storage.sign_strategy,
                remark=admin_storage.remark,
            ),
        )

        async def stage() -> SpeechPackageStageResponse:
            async with maker() as connection:
                return await speech_catalog_service.stage_package_upload(
                    connection,
                    upload=_upload(package_bytes),
                )

        try:
            stage_task = asyncio.create_task(stage())
            deadline = asyncio.get_running_loop().time() + 30
            while not await original_operator.exists(object_key):
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail('等待真实语音包上传到原存储位置超时')
                await asyncio.sleep(0.05)

            assert not stage_task.done(), '暂存未等待存储配置 advisory lock'
            await admin.commit()
            with pytest.raises(errors.ConflictError, match='存储配置在暂存登记前发生变化'):
                await stage_task
            async with maker() as orphan_check:
                assert (
                    await orphan_check.scalar(
                        select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == digest).limit(1)
                    )
                    is None
                )
            assert await original_operator.exists(object_key)
            await restore_storage_prefix()

            async with maker() as retry:
                recovered = await speech_catalog_service.stage_package_upload(
                    retry,
                    upload=_upload(package_bytes),
                )
                registered = await retry.get(HasnSpeechPackage, recovered.package_id)
                assert registered is not None
                assert registered.sha256 == digest
                assert recovered.sha256 == digest
        finally:
            if admin.in_transaction():
                await admin.rollback()
            if stage_task is not None and not stage_task.done():
                stage_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await stage_task
            await restore_storage_prefix()
            await original_operator.delete(object_key)


async def test_concurrent_same_digest_stage_is_idempotent(session: AsyncSession) -> None:
    """两个真实连接并发暂存同一内容时，共享一个不可变登记并都返回成功。"""
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    package_bytes = _real_zip_bytes(f'concurrent-stage-{_RUN_MARKER}')

    async def stage() -> SpeechPackageStageResponse:
        async with maker() as connection:
            return await speech_catalog_service.stage_package_upload(
                connection,
                upload=_upload(package_bytes),
            )

    first, second = await asyncio.gather(stage(), stage())

    assert first.package_id == second.package_id
    assert first.sha256 == second.sha256 == hashlib.sha256(package_bytes).hexdigest()
    assert {first.already_exists, second.already_exists} == {False, True}


async def test_stage_package_rejects_non_zip_bytes(session: AsyncSession) -> None:
    with pytest.raises(errors.RequestError, match='ZIP'):
        await speech_catalog_service.stage_package_upload(
            session,
            upload=_upload(b'PK fake is not a real zip'),
        )


async def test_same_size_s3_corruption_is_rejected_before_head_switch(
    session: AsyncSession,
) -> None:
    package_bytes = _real_zip_bytes()
    staged = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )
    await session.commit()
    package = await session.scalar(select(HasnSpeechPackage).where(HasnSpeechPackage.id == staged.package_id))
    assert package is not None
    corrupted = bytearray(package_bytes)
    corrupted[-1] ^= 0x01
    storage = await StorageService.get_storage(session, package.storage_id)
    operator = get_operator_for_storage(storage)
    # 绕过应用命名空间保护，模拟对象存储管理员凭据遭误用后的外部覆盖。
    await operator.write(staged.object_key, bytes(corrupted), content_type='application/zip')
    corrupted_stat = await StorageService.stat(
        session,
        storage_id=package.storage_id,
        object_key=staged.object_key,
    )
    assert corrupted_stat.etag
    # 同步登记中的版本证据，确保本用例继续深入完整 SHA-256 分支；
    # 单纯对象覆盖已由 object_etag 不一致路径更早拒绝。
    package.object_etag = corrupted_stat.etag
    await session.commit()
    sequence = max(
        int((await session.scalar(select(func.max(HasnSpeechCatalogRelease.release_sequence)))) or Decimal(0)) + 1,
        time.time_ns(),
    )
    catalog_json = _release_document(
        package_url=staged.download_url,
        sha256=staged.sha256,
        compressed_size=staged.size,
        release_sequence=sequence,
    )
    before = await session.scalar(select(HasnSpeechCatalog.revision).where(HasnSpeechCatalog.config_key == 'global'))
    try:
        with pytest.raises(errors.ServerError, match='真实对象 SHA-256'):
            await speech_catalog_service.publish_release(
                session,
                catalog_json=catalog_json,
                published_by='integration-corruption-test',
            )
        assert (
            await session.scalar(select(HasnSpeechCatalog.revision).where(HasnSpeechCatalog.config_key == 'global'))
            == before
        )
    finally:
        await operator.write(staged.object_key, package_bytes, content_type='application/zip')


async def test_missing_package_cannot_switch_head_then_complete_release_is_atomic(
    session: AsyncSession,
) -> None:
    package_bytes = _real_zip_bytes()
    staged = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )
    await session.commit()
    current_sequence = (await session.scalar(select(func.max(HasnSpeechCatalogRelease.release_sequence)))) or Decimal(0)
    sequence = max(int(current_sequence) + 1, time.time_ns())
    before = await session.scalar(select(HasnSpeechCatalog.revision).where(HasnSpeechCatalog.config_key == 'global'))
    missing_sha256 = hashlib.sha256(b'not-staged').hexdigest()
    incomplete_catalog = _release_document(
        package_url=staged.download_url,
        sha256=staged.sha256,
        compressed_size=staged.size,
        release_sequence=sequence,
        extra_unstaged_sha256=missing_sha256,
    )

    with pytest.raises(errors.RequestError, match='尚未暂存'):
        await speech_catalog_service.publish_release(
            session,
            catalog_json=incomplete_catalog,
            published_by='integration-test',
        )
    assert (
        await session.scalar(select(HasnSpeechCatalog.revision).where(HasnSpeechCatalog.config_key == 'global'))
        == before
    )

    catalog_json = _release_document(
        package_url=staged.download_url,
        sha256=staged.sha256,
        compressed_size=staged.size,
        release_sequence=sequence,
    )
    published = await speech_catalog_service.publish_release(
        session,
        catalog_json=catalog_json,
        published_by='integration-test',
    )

    head = await session.scalar(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
    assert head is not None
    assert head.revision == published.revision
    assert int(head.release_sequence or 0) == sequence
    assert head.current_release_id == published.release_id
    assert (
        await session.scalar(
            select(func.count())
            .select_from(HasnSpeechCatalogReleasePackage)
            .where(HasnSpeechCatalogReleasePackage.release_id == published.release_id)
        )
        == 1
    )
    assert published.revision == compute_revision(catalog_json)
    assert published.idempotent is False
    assert published.packages == [staged.model_copy(update={'already_exists': True})]

    node = await speech_catalog_service.get_node_response(session)
    assert node.catalog_json == catalog_json
    assert node.revision == published.revision

    release_count = await session.scalar(select(func.count()).select_from(HasnSpeechCatalogRelease))
    mapping_count = await session.scalar(select(func.count()).select_from(HasnSpeechCatalogReleasePackage))
    repeated = await speech_catalog_service.publish_release(
        session,
        catalog_json=catalog_json,
        published_by='integration-test-repeat',
    )
    assert repeated.idempotent is True
    assert repeated.release_id == published.release_id
    assert await session.scalar(select(func.count()).select_from(HasnSpeechCatalogRelease)) == release_count
    assert await session.scalar(select(func.count()).select_from(HasnSpeechCatalogReleasePackage)) == mapping_count

    conflicting_document = json.loads(catalog_json)
    conflicting_document['signature'] = 'ef' * 64
    conflicting_catalog = json.dumps(conflicting_document, separators=(',', ':'))
    with pytest.raises(errors.ConflictError, match='冲突'):
        await speech_catalog_service.publish_release(
            session,
            catalog_json=conflicting_catalog,
            published_by='integration-test-conflict',
        )

    rollback_catalog = _release_document(
        package_url=staged.download_url,
        sha256=staged.sha256,
        compressed_size=staged.size,
        release_sequence=sequence - 1,
    )
    with pytest.raises(errors.ConflictError, match='回退'):
        await speech_catalog_service.publish_release(
            session,
            catalog_json=rollback_catalog,
            published_by='integration-test-rollback',
        )

    unchanged_head = await session.scalar(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
    assert unchanged_head is not None
    assert unchanged_head.catalog_json == catalog_json
    assert unchanged_head.current_release_id == published.release_id


async def test_concurrent_release_commit_is_serialized_and_visible_across_connections(
    session: AsyncSession,
) -> None:
    """两个真实连接竞争同一序列时只允许一个提交，并由第三连接读到已提交 head。"""
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    package_bytes = _real_zip_bytes(f'concurrent-release-{_RUN_MARKER}')
    staged = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )
    await session.commit()
    sequence = time.time_ns()
    first_catalog = _release_document(
        package_url=staged.download_url,
        sha256=staged.sha256,
        compressed_size=staged.size,
        release_sequence=sequence,
    )
    second_document = json.loads(first_catalog)
    second_document['signature'] = 'ef' * 64
    second_catalog = json.dumps(second_document, separators=(',', ':'))

    async def publish(
        catalog_json: str,
        label: str,
    ) -> SpeechCatalogPublishResponse:
        async with maker() as connection:
            return await speech_catalog_service.publish_release(
                connection,
                catalog_json=catalog_json,
                published_by=label,
            )

    results = await asyncio.gather(
        publish(first_catalog, 'concurrency-first'),
        publish(second_catalog, 'concurrency-second'),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, errors.ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    published = successes[0]

    async with maker() as observer:
        head = await observer.scalar(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
        assert head is not None
        assert head.current_release_id == published.release_id
        assert int(head.release_sequence or 0) == sequence
        assert head.revision == published.revision
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(HasnSpeechCatalogRelease)
                .where(HasnSpeechCatalogRelease.release_sequence == sequence)
            )
            == 1
        )
