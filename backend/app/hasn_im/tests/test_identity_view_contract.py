"""IdentityView contract suite（R2-09·真实 PG·零 mock）。

验证 `SqlAlchemyIdentityView`（§9.3 阶段一授权身份只读视图）满足 `IdentityView` port 契约、
且 **fail-closed 语义与设计一致**（身份行缺失 / 停用即拒新消息）：

1. 结构化子类型：adapter 实现 IdentityView 契约（`resolve` 齐）；
2. `resolve`：存活 human/agent → `IdentityRef(active=True)`；停用（human suspended、agent disabled）
   → `IdentityRef(active=False)`；身份行缺失 → None；非身份前缀（群/系统）→ None；
3. `require_active`（fail-closed 发送前置）：缺失即拒、停用即拒（**停用身份发送被拒测试**），
   存活则返回 `IdentityRef`。

每用例用 uuid 派生全新 h_/a_ 身份 id，末尾清理自身行，不污染库。需要本地 PG
（export DATABASE_PORT=15432），不可达则跳过。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_im.adapters.sqlalchemy_identity_view import SqlAlchemyIdentityView
from backend.app.hasn_im.ports.identity_view import (
    IdentityRejected,
    IdentityView,
    require_active,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _dispose_global_engine():
    """每测试结束（其自身事件循环内）dispose 全局应用引擎池，根除跨 loop teardown 噪声。

    require_active / resolve 的 default 路径会走全局 async_db_session（本套件用注入替身，故
    默认不触及；此 fixture 兜底防其它导入副作用留下跨 loop 连接被 GC 误报为随机失败）。
    """
    yield
    from backend.database.db import async_engine

    await async_engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 IdentityView 契约套件：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _hid() -> str:
    return f'h_{uuid.uuid4().hex[:20]}'


def _aid() -> str:
    return f'a_{uuid.uuid4().hex[:20]}'


async def _seed_human(sessionmaker, *, hasn_id: str, status: str = 'active') -> None:
    # star_id 有唯一约束（idx_hasn_humans_star_id），默认 '' 会跨行撞键——派生唯一值。
    async with sessionmaker() as db:
        db.add(HasnHumans(hasn_id=hasn_id, star_id=hasn_id[:30], status=status))
        await db.commit()


async def _seed_agent(
    sessionmaker, *, hasn_id: str, owner_id: str, status: str = 'active'
) -> None:
    async with sessionmaker() as db:
        db.add(HasnAgents(hasn_id=hasn_id, star_id=hasn_id[:40], owner_id=owner_id, status=status))
        await db.commit()


async def _cleanup(sessionmaker, *ids: str) -> None:
    async with sessionmaker() as db:
        await db.execute(sa.delete(HasnHumans).where(HasnHumans.hasn_id.in_(ids)))
        await db.execute(sa.delete(HasnAgents).where(HasnAgents.hasn_id.in_(ids)))
        await db.commit()


async def test_is_identity_view():
    """结构化子类型检查：adapter 实现 IdentityView 契约。"""
    assert isinstance(SqlAlchemyIdentityView(), IdentityView)


async def test_resolve_active_human(sessionmaker_pg):
    h = _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_human(sessionmaker_pg, hasn_id=h, status='active')
        ref = await view.resolve(h)
        assert ref is not None
        assert ref.kind == 'human'
        assert ref.active is True
        assert ref.owner_id == h  # human 自身即主人
    finally:
        await _cleanup(sessionmaker_pg, h)


async def test_resolve_suspended_human_inactive(sessionmaker_pg):
    h = _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_human(sessionmaker_pg, hasn_id=h, status='suspended')
        ref = await view.resolve(h)
        assert ref is not None and ref.active is False
    finally:
        await _cleanup(sessionmaker_pg, h)


async def test_resolve_active_agent(sessionmaker_pg):
    a, owner = _aid(), _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_agent(sessionmaker_pg, hasn_id=a, owner_id=owner, status='active')
        ref = await view.resolve(a)
        assert ref is not None
        assert ref.kind == 'agent'
        assert ref.active is True
        assert ref.owner_id == owner
    finally:
        await _cleanup(sessionmaker_pg, a)


async def test_resolve_disabled_agent_inactive(sessionmaker_pg):
    a, owner = _aid(), _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_agent(sessionmaker_pg, hasn_id=a, owner_id=owner, status='disabled')
        ref = await view.resolve(a)
        assert ref is not None and ref.active is False
    finally:
        await _cleanup(sessionmaker_pg, a)


async def test_resolve_missing_returns_none(sessionmaker_pg):
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    assert await view.resolve(_hid()) is None
    assert await view.resolve(_aid()) is None


async def test_resolve_non_identity_prefix_returns_none(sessionmaker_pg):
    """群 / 系统主体等非身份前缀 → None（不经身份视图）。"""
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    assert await view.resolve('g:500001') is None
    assert await view.resolve('system') is None


async def test_require_active_missing_raises(sessionmaker_pg):
    """fail-closed：身份行缺失即拒新消息。"""
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    with pytest.raises(IdentityRejected):
        await require_active(view, _hid())


async def test_require_active_suspended_raises(sessionmaker_pg):
    """fail-closed 核心验收：停用身份发送被拒（human suspended）。"""
    h = _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_human(sessionmaker_pg, hasn_id=h, status='suspended')
        with pytest.raises(IdentityRejected):
            await require_active(view, h)
    finally:
        await _cleanup(sessionmaker_pg, h)


async def test_require_active_disabled_agent_raises(sessionmaker_pg):
    """fail-closed：已停用分身发送被拒（agent disabled）。"""
    a, owner = _aid(), _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_agent(sessionmaker_pg, hasn_id=a, owner_id=owner, status='disabled')
        with pytest.raises(IdentityRejected):
            await require_active(view, a)
    finally:
        await _cleanup(sessionmaker_pg, a)


async def test_require_active_passes_for_active(sessionmaker_pg):
    """存活身份通过前置并返回其 IdentityRef。"""
    h = _hid()
    view = SqlAlchemyIdentityView(session_factory=sessionmaker_pg)
    try:
        await _seed_human(sessionmaker_pg, hasn_id=h, status='active')
        ref = await require_active(view, h)
        assert ref.hasn_id == h and ref.active is True
    finally:
        await _cleanup(sessionmaker_pg, h)
