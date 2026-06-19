"""创作项目「负责分身」归属校验真实 PG 验收（CRX-3，零 mock，事务末尾回滚）。

设计事实源：docs/自媒体创作运营/00-自媒体创作运营全链路AI-Native应用设计.md（§8.4 主脑 re-bind）。

不变量（同 deck/copilot 的协作分身归属校验）：
- create_project / update_project 写入 assignee_agent_id 前，必须校验该分身归本 owner
  （HasnAgents.owner_id == scope.owner_hasn_id）；不是本人名下分身一律 NotFoundError（404 不泄露）。
- 不带 assignee_agent_id 的建/改不受影响（绑定可选）。
- owner_hasn_id 缺失（未解析出主人）时带 assignee_agent_id 一律拒（零信任边界）。

需要本地 PostgreSQL :15432（部署制品），hasn_creator 表已迁移。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn_creator.service.creator_service import CreatorService
from backend.app.hasn_creator.service.scope_context import CreatorScope
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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


async def _seed(session) -> tuple[str, int, str, str]:
    """播两个分身：own（本 owner 名下）+ foreign（另一 owner 名下）。返回 (owner, uid, own, foreign)。"""
    tag = _uid()
    owner = f'h_crx_{tag}'
    other = f'h_crx_other_{tag}'
    uid = 960000 + int(uuid.uuid4().int % 9000)
    own_agent = f'a_crx_own_{tag}'
    foreign_agent = f'a_crx_foreign_{tag}'
    session.add_all([
        HasnAgents(
            hasn_id=own_agent,
            star_id=f's_{tag}_own',
            owner_id=owner,
            display_name='我的运营官',
            agent_name='op',
            status='active',
        ),
        HasnAgents(
            hasn_id=foreign_agent,
            star_id=f's_{tag}_fo',
            owner_id=other,
            display_name='别人的分身',
            agent_name='op2',
            status='active',
        ),
    ])
    await session.flush()
    return owner, uid, own_agent, foreign_agent


async def test_create_with_own_agent_ok(session) -> None:
    owner, uid, own_agent, _foreign = await _seed(session)
    scope = CreatorScope(user_id=uid, owner_hasn_id=owner)
    res = await CreatorService.create_project(
        session, user_id=uid, scope=scope, name='号A', assignee_agent_id=own_agent
    )
    assert res['assignee_agent_id'] == own_agent


async def test_create_with_foreign_agent_rejected(session) -> None:
    owner, uid, _own, foreign = await _seed(session)
    scope = CreatorScope(user_id=uid, owner_hasn_id=owner)
    with pytest.raises(errors.NotFoundError):
        await CreatorService.create_project(session, user_id=uid, scope=scope, name='号B', assignee_agent_id=foreign)


async def test_create_without_agent_ok(session) -> None:
    owner, uid, _own, _foreign = await _seed(session)
    scope = CreatorScope(user_id=uid, owner_hasn_id=owner)
    res = await CreatorService.create_project(session, user_id=uid, scope=scope, name='号C')
    assert not res.get('assignee_agent_id')


async def test_update_rebind_own_ok_foreign_rejected(session) -> None:
    owner, uid, own_agent, foreign = await _seed(session)
    scope = CreatorScope(user_id=uid, owner_hasn_id=owner)
    proj = await CreatorService.create_project(session, user_id=uid, scope=scope, name='号D')
    pid = int(proj['id'])

    ok = await CreatorService.update_project(
        session, user_id=uid, scope=scope, project_id=pid, fields={'assignee_agent_id': own_agent}
    )
    assert ok['assignee_agent_id'] == own_agent

    with pytest.raises(errors.NotFoundError):
        await CreatorService.update_project(
            session, user_id=uid, scope=scope, project_id=pid, fields={'assignee_agent_id': foreign}
        )


async def test_missing_owner_hasn_id_rejects_binding(session) -> None:
    _owner, uid, own_agent, _foreign = await _seed(session)
    # owner_hasn_id 未解析出（None）→ 即便是真实存在的分身也拒绝绑定（无法核实归属）。
    scope = CreatorScope(user_id=uid, owner_hasn_id=None)
    with pytest.raises(errors.NotFoundError):
        await CreatorService.create_project(session, user_id=uid, scope=scope, name='号E', assignee_agent_id=own_agent)
