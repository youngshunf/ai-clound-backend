"""SPCAT-4 — 通用语音模型签名目录发布 + 节点下发真实测试。

覆盖：
- ``compute_revision`` 纯函数：catalog 原文逐字节指纹，内容变即指纹变。
- ``publish`` 服务：四项一致性预检（URL 指向 object_key、zip sha256 对拍、https、落桶直链一致）→
  zip 落 public 桶 → catalog **逐字节原文**入库 + bump revision。
- ``get_node_response`` 节点下发：**byte-identical** 取回 catalog_json（哑存储核心不变式——
  云端不解析后重序列化，任何字段增删都会破坏 daemon 验签）。

真实 PG（DATABASE_PORT=15432）+ 真 S3Storage public 行；仅桩掉 ``write_bytes``（不打真实对象存储）
与 ``bump``（不依赖 ws/redis），其余全真。事务末尾回滚不污染库。
"""

from __future__ import annotations

import hashlib
import json

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.schema.hasn_speech_catalog import SpeechCatalogNodeResponse
from backend.app.hasn.service.speech_catalog_service import compute_revision, speech_catalog_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.model.storage import S3Storage
from backend.plugin.s3.service.storage_service import StorageService
from backend.plugin.s3.utils.file_ops import build_object_url

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 纯函数：compute_revision（无 db）
# ---------------------------------------------------------------------------
def test_compute_revision_is_verbatim_fingerprint() -> None:
    a = '{"payload":{"catalog_version":"1"},"signature":"sig"}'
    b = '{"payload":{"catalog_version":"1"},"signature":"sig"}'
    c = '{"payload":{"catalog_version":"2"},"signature":"sig"}'
    # 逐字节原文相同 → 指纹相同；内容变（含换版本、重签）→ 指纹变。
    assert compute_revision(a) == compute_revision(b)
    assert compute_revision(a) != compute_revision(c)
    assert compute_revision(a) == hashlib.sha256(a.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 服务：publish + get_node_response（真实 PG + 桩 write_bytes/bump）
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    # 确保单行权威表存在（无 FK 依赖，随测建表幂等，不污染其它表）。
    async with engine.begin() as conn:
        await conn.run_sync(HasnSpeechCatalog.__table__.create, checkfirst=True)

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _ensure_public_storage(session: AsyncSession, tag: str) -> S3Storage:
    """取库里首个 public 桶（_pick_storage 选它）；无则补一行 https 兜底。"""
    has_public = (
        await session.execute(select(S3Storage.id).where(S3Storage.access == 'public').limit(1))
    ).first()
    if not has_public:
        session.add(
            S3Storage(name=f'pub-{tag}', access='public', bucket=f'b-pub-{tag}', cdn_domain='https://cdn.test')
        )
        await session.flush()
    storages = await StorageService._storages(session)  # noqa: SLF001（测试内复用 service 选桶逻辑）
    return next(s for s in storages if getattr(s, 'access', 'private') == 'public')


def _build_signed_catalog(url: str, sha256: str) -> str:
    """构造签名 catalog 原文（cloud 不验签，signature 用占位）；URL/sha256 与将上传的 zip 对拍。"""
    payload = {
        'catalog_version': '2024-07-17',
        'issued_at': '2024-07-17T00:00:00Z',
        'models': [
            {
                'model_id': 'sensevoice-small-int8',
                'display_name': 'SenseVoice Small (int8)',
                'model_version': '2024-07-17',
                'engine': 'sherpa_onnx',
                'packages': [
                    {
                        'platform': {'os': 'macos', 'arch': 'arm64', 'acceleration': 'cpu'},
                        'url': url,
                        'sha256': sha256,
                        'signature': 'pkg-sig-placeholder',
                        'compressed_size': 100,
                        'installed_size': 200,
                    }
                ],
            }
        ],
    }
    # 逐字节原文即 daemon 验签对象；这里用紧凑分隔符固定成一份确定文本。
    return json.dumps({'payload': payload, 'signature': 'catalog-sig-placeholder'}, separators=(',', ':'))


async def test_publish_then_node_delivery_is_byte_identical(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uuid

    from backend.app.hasn.service import sync_invalidate_service
    from backend.plugin.s3.service import storage_service as svc_mod

    tag = uuid.uuid4().hex[:8]
    storage = await _ensure_public_storage(session, tag)

    # 对象 key + 将上传的 zip 字节；据 storage 现拼「落桶直链」，回填进 catalog 后再签名——
    # 保证发布方声明 URL == 服务端落桶直链（预检 4）。
    object_key = f'speech/sensevoice-small-int8/2024-07-17/model-{tag}.zip'
    expected_url = build_object_url(storage, object_key)
    if not expected_url.startswith('https://'):
        pytest.skip(f'首个 public 桶非 https（{expected_url}），跳过（ATS 预检需 https）')

    zip_bytes = b'PK\x03\x04 fake-speech-model ' + tag.encode()
    actual_sha = hashlib.sha256(zip_bytes).hexdigest()
    catalog_json = _build_signed_catalog(expected_url, actual_sha)

    # 桩：不打真实对象存储；不触发 ws/redis 推送。
    write_calls: list[int] = []

    async def _fake_write_bytes(storage, object_key, data, content_type) -> None:  # noqa: ANN001, RUF029
        write_calls.append(len(data))

    monkeypatch.setattr(svc_mod, 'write_bytes', _fake_write_bytes)
    bump_calls: list[str] = []

    async def _fake_bump(kind, db, *, owner_id=None) -> str:  # noqa: ANN001, RUF029
        bump_calls.append(kind)
        return 'rev-stub'

    monkeypatch.setattr(sync_invalidate_service, 'bump', _fake_bump)

    resp = await speech_catalog_service.publish(
        session,
        catalog_json=catalog_json,
        zip_bytes=zip_bytes,
        object_key=object_key,
        published_by='ci',
    )

    # 发布出参：服务端权威 sha256/size + 公开 https 直链 + 摘要。
    assert resp.sha256 == actual_sha
    assert resp.size == len(zip_bytes)
    assert resp.download_url == expected_url
    assert resp.object_key == object_key
    assert resp.revision == compute_revision(catalog_json)
    assert resp.catalog_version == '2024-07-17'
    assert len(resp.models) == 1
    assert resp.models[0].model_id == 'sensevoice-small-int8'
    assert resp.models[0].platforms == ['macos-arm64-cpu']
    assert write_calls == [len(zip_bytes)]  # 真上传了一次

    # ★ 哑存储核心不变式：节点下发的 catalog_json 与发布时**逐字节相同**。
    node: SpeechCatalogNodeResponse = await speech_catalog_service.get_node_response(session)
    assert node.catalog_json == catalog_json  # byte-identical
    assert node.revision == resp.revision
    assert node.catalog_version == '2024-07-17'
    assert node.published_time is not None

    # sync 指纹与 node revision 一致（供 get_all_revisions 握手快照）。
    assert await speech_catalog_service.get_revision(session) == resp.revision

    # DB 持久化核实：单行 catalog_json 就是原文。
    row = (
        await session.execute(select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == 'global'))
    ).scalar_one()
    assert row.catalog_json == catalog_json


async def test_node_response_empty_when_unpublished(session: AsyncSession) -> None:
    # 未发布（无行）→ 返回空，daemon 保持未装配态（零 fake）。
    node = await speech_catalog_service.get_node_response(session)
    assert node.catalog_json is None
    assert node.revision == ''
    assert await speech_catalog_service.get_revision(session) == ''


async def test_publish_rejects_sha256_mismatch(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.plugin.s3.service import storage_service as svc_mod

    async def _fake_write_bytes(*a, **k) -> None:  # noqa: RUF029
        raise AssertionError('sha256 不匹配时不应触发上传')

    monkeypatch.setattr(svc_mod, 'write_bytes', _fake_write_bytes)

    object_key = 'speech/sensevoice-small-int8/2024-07-17/x.zip'
    # catalog 声明的 sha256 故意不匹配将上传的 zip。
    catalog_json = _build_signed_catalog(f'https://cdn.test/{object_key}', 'deadbeef' * 8)
    with pytest.raises(errors.RequestError, match='sha256'):
        await speech_catalog_service.publish(
            session,
            catalog_json=catalog_json,
            zip_bytes=b'real-bytes',
            object_key=object_key,
            published_by='ci',
        )


async def test_publish_rejects_url_not_referencing_object_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.plugin.s3.service import storage_service as svc_mod

    async def _fake_write_bytes(*a, **k) -> None:  # noqa: RUF029
        raise AssertionError('URL 不指向 object_key 时不应触发上传')

    monkeypatch.setattr(svc_mod, 'write_bytes', _fake_write_bytes)

    zip_bytes = b'real-bytes'
    sha = hashlib.sha256(zip_bytes).hexdigest()
    # catalog 内 URL 指向另一个 key，与本次 object_key 不符 → 预检 1 拒绝。
    catalog_json = _build_signed_catalog('https://cdn.test/speech/other/other.zip', sha)
    with pytest.raises(errors.RequestError, match='object_key'):
        await speech_catalog_service.publish(
            session,
            catalog_json=catalog_json,
            zip_bytes=zip_bytes,
            object_key='speech/sensevoice-small-int8/2024-07-17/mine.zip',
            published_by='ci',
        )
