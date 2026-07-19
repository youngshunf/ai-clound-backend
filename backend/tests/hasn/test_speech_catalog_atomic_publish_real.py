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

import pytest
import pytest_asyncio

from fastapi import UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.datastructures import Headers

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.model.hasn_speech_catalog_release import HasnSpeechCatalogRelease
from backend.app.hasn.model.hasn_speech_catalog_release_package import (
    HasnSpeechCatalogReleasePackage,
)
from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage
from backend.app.hasn.schema.hasn_speech_catalog import SpeechCatalogPublishResponse
from backend.app.hasn.service.speech_catalog_service import (
    build_speech_package_object_key,
    compute_revision,
    speech_catalog_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


def _require_real_s3_test() -> None:
    """只有显式授权的环境才运行真实对象写入。"""
    if os.getenv('HASN_SPEECH_REAL_S3_TEST') != '1':
        pytest.skip('需显式设置 HASN_SPEECH_REAL_S3_TEST=1 才允许写入并清理真实测试对象')


def _real_zip_bytes(marker: str = 'default') -> bytes:
    """生成确定且可被标准 ZIP 读取器打开的最小模型包。"""
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
    """显式门控真实 PostgreSQL/S3，用例结束时回滚业务行并删除测试对象。"""
    _require_real_s3_test()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'真实 PostgreSQL 不可达，跳过: {exc!r}')
    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        try:
            digest = hashlib.sha256(_real_zip_bytes()).hexdigest()
            package = await db.scalar(select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == digest).limit(1))
            if package is not None:
                await StorageService.delete_object(
                    db,
                    storage_id=package.storage_id,
                    object_key=package.object_key,
                )
        finally:
            await db.rollback()
            await db.close()
            await engine.dispose()


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
    package = await session.scalar(select(HasnSpeechPackage).where(HasnSpeechPackage.id == staged.package_id))
    assert package is not None
    corrupted = bytearray(package_bytes)
    corrupted[-1] ^= 0x01
    await StorageService.upload(
        session,
        bytes(corrupted),
        category='speech_model',
        filename='integration-corrupted.zip',
        content_type='application/zip',
        key=staged.object_key,
    )
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
        await StorageService.upload(
            session,
            package_bytes,
            category='speech_model',
            filename='integration-restored.zip',
            content_type='application/zip',
            key=staged.object_key,
        )


async def test_missing_package_cannot_switch_head_then_complete_release_is_atomic(
    session: AsyncSession,
) -> None:
    package_bytes = _real_zip_bytes()
    staged = await speech_catalog_service.stage_package_upload(
        session,
        upload=_upload(package_bytes),
    )
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


async def test_concurrent_release_commit_is_serialized_and_visible_across_connections() -> None:
    """两个真实连接竞争同一序列时只允许一个提交，并由第三连接读到已提交 head。"""
    _require_real_s3_test()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    package_bytes = _real_zip_bytes('concurrent-release')
    digest = hashlib.sha256(package_bytes).hexdigest()
    staged = None
    previous_head: dict | None = None
    release_id: int | None = None
    package_storage_id: int | None = None
    try:
        async with maker() as setup:
            async with setup.begin():
                if (
                    await setup.scalar(select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == digest).limit(1))
                ) is not None:
                    pytest.fail('并发测试内容摘要已存在，拒绝复用非本次测试登记')
                head = await setup.scalar(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
                if head is not None:
                    previous_head = {
                        'catalog_json': head.catalog_json,
                        'revision': head.revision,
                        'catalog_version': head.catalog_version,
                        'current_release_id': head.current_release_id,
                        'release_sequence': head.release_sequence,
                        'key_id': head.key_id,
                        'model_summary': head.model_summary,
                        'published_by': head.published_by,
                        'updated_time': head.updated_time,
                    }
                staged = await speech_catalog_service.stage_package_upload(
                    setup,
                    upload=_upload(package_bytes),
                )
                package = await setup.get(HasnSpeechPackage, staged.package_id)
                assert package is not None
                package_storage_id = package.storage_id
                current_sequence = (
                    await setup.scalar(select(func.max(HasnSpeechCatalogRelease.release_sequence)))
                ) or Decimal(0)

        assert staged is not None
        sequence = max(int(current_sequence) + 1, time.time_ns())
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
                async with connection.begin():
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
        if successes:
            release_id = successes[0].release_id
        assert len(successes) == 1
        assert len(conflicts) == 1
        published = successes[0]

        async with maker() as observer:
            head = await observer.scalar(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
            assert head is not None
            assert head.current_release_id == release_id
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
    finally:
        try:
            async with maker() as cleanup:
                async with cleanup.begin():
                    current_head = await cleanup.scalar(
                        select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global')
                    )
                    if current_head is not None and release_id is not None:
                        if previous_head is None:
                            await cleanup.delete(current_head)
                            await cleanup.flush()
                        else:
                            await cleanup.execute(
                                update(HasnSpeechCatalog)
                                .where(HasnSpeechCatalog.id == current_head.id)
                                .values(**previous_head)
                            )
                            await cleanup.flush()
                    if release_id is not None:
                        await cleanup.execute(
                            delete(HasnSpeechCatalogReleasePackage).where(
                                HasnSpeechCatalogReleasePackage.release_id == release_id
                            )
                        )
                        await cleanup.execute(
                            delete(HasnSpeechCatalogRelease).where(HasnSpeechCatalogRelease.id == release_id)
                        )
                    package = await cleanup.scalar(
                        select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == digest).limit(1)
                    )
                    if package is not None:
                        await cleanup.delete(package)
            if package_storage_id is not None:
                async with maker() as storage_session:
                    await StorageService.delete_object(
                        storage_session,
                        storage_id=package_storage_id,
                        object_key=build_speech_package_object_key(digest),
                    )
        finally:
            await engine.dispose()
