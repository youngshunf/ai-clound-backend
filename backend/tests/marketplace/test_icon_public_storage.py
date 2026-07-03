"""图标上传落公共桶——`_resolve_public_storage_id` 解析（真实 PG，零 mock）。

图标是公开展示资产：未显式指定存储时必须落 access='public' 的桶，否则 URL 落到
默认私有桶、浏览器直取 401、前端表现为占位图（本次修复的根因）。

需要 export DATABASE_PORT=15432（本地 PG）。事务末尾回滚不污染库。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.model.storage import S3Storage

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
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


async def test_resolve_prefers_public_storage(session) -> None:
    """未指定 storage_id → 解析到 access='public' 的桶（而非默认私有桶）。"""
    tag = uuid.uuid4().hex[:8]
    session.add(S3Storage(name=f'priv-{tag}', access='private', bucket=f'b-priv-{tag}'))
    session.add(S3Storage(name=f'pub-{tag}', access='public', bucket=f'b-pub-{tag}'))
    await session.flush()

    resolved = await marketplace_storage_service._resolve_public_storage_id(session, None)
    assert resolved is not None
    row = await s3_storage_dao.get(session, resolved)
    assert row is not None
    assert row.access == 'public'


async def test_resolve_passthrough_explicit_id(session) -> None:
    """显式给 storage_id → 原样返回（不覆盖调用方意图，如包文件仍可落私有桶）。"""
    resolved = await marketplace_storage_service._resolve_public_storage_id(session, 987654)
    assert resolved == 987654
