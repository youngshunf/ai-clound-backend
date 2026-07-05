"""G1 特权授予源真实 PG 验收（doc18 §4.1 · 实施/103 U2）——零 mock。

覆盖「授予源仅 Admin 表 ∪ ENV bootstrap」的活取 + 合并 + 缓存失效链路：
1. 表授予活取（get_privileged_grants_from_db 真查 hasn_platform_operator_grants）；
2. 缓存合并（get_privileged_grants_cached = 表 ∪ ENV，短 TTL）；
3. ENV bootstrap 现算合并（不进缓存、无表行也生效）；
4. service 层守卫：非特权 scope 一律拒（防 Admin 误灌普通能力进特权源）；
5. 授予/撤销经 service 即时清缓存（下次活取立即反映）。

需本地 PostgreSQL :15432 + Redis。表 hasn_platform_operator_grants 已建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_platform_operator_grants import (
    CreateHasnPlatformOperatorGrantsParam,
    DeleteHasnPlatformOperatorGrantsParam,
)
from backend.app.hasn.service.hasn_platform_operator_grants_service import (
    hasn_platform_operator_grants_service,
)
from backend.common.exception import errors
from backend.common.security.agent_jwt import (
    get_privileged_grants_cached,
    get_privileged_grants_from_db,
    invalidate_privileged_grants_cache,
)
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.database.redis import redis_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 用高熵前缀的测试分身，避免撞真实数据
AGENT_A = 'a_g1pg_operator_x1'
AGENT_B = 'a_g1pg_envonly_x2'


async def _purge(db: AsyncSession, agent_ids: list[str]) -> None:
    for aid in agent_ids:
        await db.execute(
            text('DELETE FROM hasn_platform_operator_grants WHERE agent_hasn_id = :aid'),
            {'aid': aid},
        )
        await invalidate_privileged_grants_cache(aid)
    await db.commit()


async def _reset_redis_pool() -> None:
    # pytest-asyncio 每测一个事件循环；全局 redis_client 连接池绑首个用它的循环，跨循环复用会
    # 'Event loop is closed'。每测重连让 redis 调用落在本循环（与 test_capability_ticket 同法）。
    try:
        await redis_client.connection_pool.disconnect()
    except Exception:
        pass


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    await async_engine.dispose()
    await _reset_redis_pool()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(sess, [AGENT_A, AGENT_B])
        yield sess
    finally:
        await _purge(sess, [AGENT_A, AGENT_B])
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        await async_engine.dispose()
        await _reset_redis_pool()


async def test_grant_landed_and_fetched_from_db(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """service.create 落库后 get_privileged_grants_from_db 真查得到。"""
    monkeypatch.setattr(settings, 'PLATFORM_OPERATOR_AGENTS', '', raising=False)
    await hasn_platform_operator_grants_service.create(
        db=session,
        obj=CreateHasnPlatformOperatorGrantsParam(
            agent_hasn_id=AGENT_A, scope='diag:read:all', granted_by='admin_test', note='验收'
        ),
    )
    await session.commit()

    rows = await get_privileged_grants_from_db(session, AGENT_A)
    assert 'diag:read:all' in rows
    # 别的分身不串号
    assert await get_privileged_grants_from_db(session, AGENT_B) == []


async def test_cached_merges_table_and_env(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_privileged_grants_cached = 表 ∪ ENV bootstrap（ENV 现算不进缓存）。"""
    # 表里给 AGENT_A 一条 diag:manage；ENV 里另给 AGENT_A 一条 ops:*（bootstrap 兜底）
    await hasn_platform_operator_grants_service.create(
        db=session,
        obj=CreateHasnPlatformOperatorGrantsParam(
            agent_hasn_id=AGENT_A, scope='diag:manage', granted_by='admin_test', note=None
        ),
    )
    await session.commit()
    monkeypatch.setattr(settings, 'PLATFORM_OPERATOR_AGENTS', f'{AGENT_A}:ops:*', raising=False)

    granted = await get_privileged_grants_cached(AGENT_A, session)
    assert 'diag:manage' in granted  # 表
    assert 'ops:*' in granted  # ENV bootstrap


async def test_env_bootstrap_without_table_row(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """无任何表行，仅 ENV bootstrap 也能授予（应急兜底路径）。"""
    monkeypatch.setattr(settings, 'PLATFORM_OPERATOR_AGENTS', f'{AGENT_B}:diag:read:all', raising=False)
    granted = await get_privileged_grants_cached(AGENT_B, session)
    assert granted == frozenset({'diag:read:all'})


async def test_service_rejects_non_privileged_scope(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """守卫：service 层拒绝把非特权 scope 灌进特权授予源。"""
    monkeypatch.setattr(settings, 'PLATFORM_OPERATOR_AGENTS', '', raising=False)
    with pytest.raises(errors.RequestError):
        await hasn_platform_operator_grants_service.create(
            db=session,
            obj=CreateHasnPlatformOperatorGrantsParam(
                agent_hasn_id=AGENT_A, scope='media:generate', granted_by='admin_test', note=None
            ),
        )
    # * 不在末段也拒
    with pytest.raises(errors.RequestError):
        await hasn_platform_operator_grants_service.create(
            db=session,
            obj=CreateHasnPlatformOperatorGrantsParam(
                agent_hasn_id=AGENT_A, scope='ops:*:x', granted_by='admin_test', note=None
            ),
        )


async def test_revoke_invalidates_cache(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """撤销经 service.delete 即时清缓存：下次活取不再含被撤销的授予。"""
    monkeypatch.setattr(settings, 'PLATFORM_OPERATOR_AGENTS', '', raising=False)
    await hasn_platform_operator_grants_service.create(
        db=session,
        obj=CreateHasnPlatformOperatorGrantsParam(
            agent_hasn_id=AGENT_A, scope='diag:read:all', granted_by='admin_test', note=None
        ),
    )
    await session.commit()

    # 先读一次预热缓存
    granted = await get_privileged_grants_cached(AGENT_A, session)
    assert 'diag:read:all' in granted
    # 确认 redis 已缓存
    assert await redis_client.get(f'privileged_grants:{AGENT_A}') is not None

    # 取该分身现有行 id → 撤销
    rows = (
        await session.execute(
            text('SELECT id FROM hasn_platform_operator_grants WHERE agent_hasn_id = :aid'),
            {'aid': AGENT_A},
        )
    ).fetchall()
    pks = [r[0] for r in rows]
    assert pks
    await hasn_platform_operator_grants_service.delete(
        db=session, obj=DeleteHasnPlatformOperatorGrantsParam(pks=pks)
    )
    await session.commit()

    # 缓存已被 service 清除；再取应为空
    assert await redis_client.get(f'privileged_grants:{AGENT_A}') is None
    granted2 = await get_privileged_grants_cached(AGENT_A, session)
    assert 'diag:read:all' not in granted2
