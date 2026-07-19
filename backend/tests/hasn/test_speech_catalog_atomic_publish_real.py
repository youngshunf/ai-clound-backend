"""语音内容寻址暂存与原子 release 的真实 PostgreSQL/S3 集成测试。"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.model.hasn_speech_catalog_release import HasnSpeechCatalogRelease
from backend.app.hasn.model.hasn_speech_catalog_release_package import (
    HasnSpeechCatalogReleasePackage,
)
from backend.app.hasn.service.speech_catalog_service import (
    build_speech_package_object_key,
    compute_revision,
    speech_catalog_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _real_zip_bytes() -> bytes:
    """生成确定且可被标准 ZIP 读取器打开的最小模型包。"""
    output = io.BytesIO()
    info = zipfile.ZipInfo('manifest.json', date_time=(2026, 7, 19, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(output, mode='w') as archive:
        archive.writestr(info, b'{"schema_version":1}')
    return output.getvalue()


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
    """连接真实 PostgreSQL，并在用例结束时回滚业务行。"""
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
        await db.rollback()
        await db.close()
        await engine.dispose()


async def test_stage_package_is_real_s3_backed_and_idempotent(session: AsyncSession) -> None:
    package_bytes = _real_zip_bytes()
    expected_sha256 = hashlib.sha256(package_bytes).hexdigest()

    first = await speech_catalog_service.stage_package(
        session,
        package_bytes=package_bytes,
        content_type='application/zip',
    )
    second = await speech_catalog_service.stage_package(
        session,
        package_bytes=package_bytes,
        content_type='application/zip',
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
        await speech_catalog_service.stage_package(
            session,
            package_bytes=b'PK fake is not a real zip',
            content_type='application/zip',
        )


async def test_missing_package_cannot_switch_head_then_complete_release_is_atomic(
    session: AsyncSession,
) -> None:
    package_bytes = _real_zip_bytes()
    staged = await speech_catalog_service.stage_package(
        session,
        package_bytes=package_bytes,
        content_type='application/zip',
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
