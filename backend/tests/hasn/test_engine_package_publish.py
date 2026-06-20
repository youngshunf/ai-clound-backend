"""FILMPUB — downloadable_local 引擎包一键发布（上传公共桶 + 写 config_json.engine）真实测试。

覆盖：
- ``merge_engine_package`` 纯函数：首发建 engine、同版本累积多架构、版本跃迁清旧 packages。
- ``publish_engine_package`` 服务：服务端权威算 sha256/size → 落 public 桶 → 并入 config_json.engine →
  sync_bump。真实 PG（catalog 行经 ensure_catalog_seeded 播种 + 真 S3Storage public 行），仅桩掉
  ``write_bytes``（不打真实对象存储）与 ``bump``（不依赖 ws/redis），其余全真。

需要本地 PG（DATABASE_PORT=15432）。事务末尾回滚不污染库。
"""

from __future__ import annotations

import hashlib
import uuid

from typing import TYPE_CHECKING, NoReturn

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service import app_catalog_service
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.model.storage import S3Storage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 纯函数：merge_engine_package（无 db）
# ---------------------------------------------------------------------------
def test_merge_creates_engine_from_empty() -> None:
    out = app_catalog_service.merge_engine_package(
        None, os_arch='darwin-aarch64', version='1.0.0', key='k', url='u', sha256='h', size=1
    )
    assert out['engine']['version'] == '1.0.0'
    assert out['engine']['packages'] == {
        'darwin-aarch64': {'key': 'k', 'url': 'u', 'sha256': 'h', 'size': 1}
    }


def test_merge_accumulates_arches_same_version() -> None:
    c1 = app_catalog_service.merge_engine_package(
        {'models': {'llm': ['x']}},  # 不相干字段须保留
        os_arch='darwin-aarch64',
        version='1.0.0',
        key='k1',
        url='u1',
        sha256='h1',
        size=1,
    )
    c2 = app_catalog_service.merge_engine_package(
        c1, os_arch='linux-x86_64', version='1.0.0', key='k2', url='u2', sha256='h2', size=2
    )
    assert sorted(c2['engine']['packages']) == ['darwin-aarch64', 'linux-x86_64']
    assert c2['models'] == {'llm': ['x']}  # 其它配置不被吞


def test_merge_version_bump_resets_packages() -> None:
    c1 = app_catalog_service.merge_engine_package(
        None, os_arch='darwin-aarch64', version='1.0.0', key='k1', url='u1', sha256='h1', size=1
    )
    c2 = app_catalog_service.merge_engine_package(
        c1, os_arch='linux-x86_64', version='2.0.0', key='k2', url='u2', sha256='h2', size=2
    )
    # 版本跃迁：旧 darwin-aarch64 包不属于 2.0.0，被清掉。
    assert c2['engine']['version'] == '2.0.0'
    assert list(c2['engine']['packages']) == ['linux-x86_64']


# ---------------------------------------------------------------------------
# 服务：publish_engine_package（真实 PG + 桩 write_bytes/bump）
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

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def test_publish_uploads_public_and_writes_engine_config(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.hasn.service import sync_invalidate_service
    from backend.plugin.s3.service import storage_service as svc_mod

    # catalog 行（取 film，必经 seed 存在）；public S3Storage 用库里已配的真实公共桶
    # （_pick_storage 选首个 access='public'，本地库已有 hasn-pub-cdn）——若库无公共桶则补一行兜底。
    tag = uuid.uuid4().hex[:8]
    has_public = (
        await session.execute(select(S3Storage.id).where(S3Storage.access == 'public').limit(1))
    ).first()
    if not has_public:
        session.add(
            S3Storage(name=f'pub-{tag}', access='public', bucket=f'b-pub-{tag}', cdn_domain='https://cdn.test')
        )
    await app_catalog_service.ensure_catalog_seeded(session)
    await session.flush()
    catalog = (
        await session.execute(select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'film'))
    ).scalar_one()

    # 桩：不打真实对象存储；不触发 ws/redis 推送（只断言被 await）。
    write_calls: list[tuple[str, int]] = []

    # 桩替换被 service await 的真 write_bytes，故必须 async（RUF029 误报）。
    async def _fake_write_bytes(storage, object_key, data, content_type) -> None:  # noqa: ANN001, RUF029
        write_calls.append((object_key, len(data)))

    monkeypatch.setattr(svc_mod, 'write_bytes', _fake_write_bytes)
    bump_calls: list[str] = []

    async def _fake_bump(kind, db, *, owner_id=None) -> str:  # noqa: ANN001, RUF029
        bump_calls.append(kind)
        return 'rev-stub'

    monkeypatch.setattr(sync_invalidate_service, 'bump', _fake_bump)

    data = b'PK\x03\x04 fake-engine-zip-bytes ' + tag.encode()
    expected_sha = hashlib.sha256(data).hexdigest()

    engine = await app_catalog_service.publish_engine_package(
        session,
        pk=catalog.id,
        os_arch='darwin-aarch64',
        version='0.9.0',
        data=data,
        filename='film-darwin-aarch64-0.9.0.zip',
        expected_sha256=expected_sha,
    )

    # 服务端权威 sha256/size + 公共 CDN 直读 URL（不签名）+ 已落 config_json.engine。
    # URL 域名取决于库里首个 public 桶（本地多为真实 hasn-pub-cdn），故只断言「公开 http(s)、
    # 含本次对象 key、.zip 收尾」这类与桶无关的不变式，不绑死具体域名。
    pkg = engine['packages']['darwin-aarch64']
    assert engine['version'] == '0.9.0'
    assert pkg['sha256'] == expected_sha
    assert pkg['size'] == len(data)
    assert pkg['url'].startswith(('http://', 'https://'))
    assert 'film-engine/film/0.9.0/' in pkg['url']  # service 拼的对象 key 进了直读 URL
    assert pkg['url'].endswith('.zip')
    assert pkg['key'] == 'film-engine/film/0.9.0/film-darwin-aarch64-0.9.0.zip'
    assert write_calls and write_calls[0][1] == len(data)  # 真上传了
    assert bump_calls == ['platform_config']  # push 全网 daemon
    # 持久化核实：DB 里 catalog.config_json.engine 已写入。
    refreshed = (
        await session.execute(select(HasnAppCatalog).where(HasnAppCatalog.id == catalog.id))
    ).scalar_one()
    assert refreshed.config_json['engine']['packages']['darwin-aarch64']['sha256'] == expected_sha


async def test_publish_rejects_sha256_mismatch(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.common.exception import errors
    from backend.plugin.s3.service import storage_service as svc_mod

    async def _fake_write_bytes(*a, **k) -> NoReturn:  # noqa: RUF029
        raise AssertionError('sha256 不匹配时不应触发上传')

    monkeypatch.setattr(svc_mod, 'write_bytes', _fake_write_bytes)
    await app_catalog_service.ensure_catalog_seeded(session)
    await session.flush()
    catalog = (
        await session.execute(select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'film'))
    ).scalar_one()

    with pytest.raises(errors.RequestError):
        await app_catalog_service.publish_engine_package(
            session,
            pk=catalog.id,
            os_arch='darwin-aarch64',
            version='0.9.0',
            data=b'real-bytes',
            filename='x.zip',
            expected_sha256='deadbeef',  # 故意不匹配
        )
