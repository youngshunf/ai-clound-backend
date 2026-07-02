"""App 工具云端网关写类「提交边界」回归（零 mock，真实 PG :15432）。

钉死 bug：`AppTool.execute`（MCP 直连面，分身经 cloud 直连调云端 App 工具的唯一路径）此前用
``async_db_session()`` —— 退出**只 close 不 commit**。而云端 AI-Native 网关 `call_tool` 全程在该
session 内只 `flush`（业务行 + 审计行）、从不自己 commit（HTTP 工具调用面靠 `CurrentSessionTransaction`
= `async_db_session.begin()` 自动提交）。两条到达面 session 语义不一致 → 经 MCP 直连面的 App 写类工具
（creator/knowledge/... 凡 handler 自身不 commit 的，如 creator service 只 flush）**返回成功却整体
回滚不落库**：`hasn.creator.project.create` 返回完整 id，随后 `project.list`/`content.create` 却查不到。

修复：`app_tools.py` 改用 ``async_db_session.begin()``（与 HTTP 面 `CurrentSessionTransaction` 同语义、
自动提交）。本测试经**真实 AppTool.execute** 建项目，再在**独立 session**确认它真落库——不提交则查不到
（修前红、修后绿）。社区 handler 各自 commit 故修前也能落库，但 creator/knowledge 等不在此列。

需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.model.profile import Profile
from backend.app.hasn_creator.model.project import Project
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.app_tool_loader import load_published_app_tools
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _agent() -> AgentTokenPayload:
    # 合成身份：MCP 直连面恒取 request.state.agent，不查 Redis/DB；owner_user_id 用高位测试值避免撞真实数据。
    return AgentTokenPayload(
        agent_hasn_id='hasn:agent:appcommit-x',
        agent_name='提交边界回归分身',
        owner_hasn_id='hasn:owner:appcommit-a',
        owner_user_id=920077,
        session_uuid='sess-appcommit-test',
        expire_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    )


async def _pg_reachable() -> bool:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        return False
    else:
        return True
    finally:
        await engine.dispose()


async def test_app_tool_execute_commits_creator_project_write() -> None:
    """真实 AppTool.execute(hasn.creator.project.create) 写入须在独立 session 可见（=已提交）。"""
    if not await _pg_reachable():
        pytest.skip('本地 PostgreSQL :15432 不可达，跳过')

    tools = await load_published_app_tools()
    tool = next((t for t in tools if t.name == 'hasn.creator.project.create'), None)
    assert tool is not None, 'creator project.create AppTool 未在已发布工具中（builtin manifest 应含）'

    name = f'[工具测试-commit] {uuid.uuid4().hex[:8]}'
    agent_ctx = AgentContext.from_token_payload(_agent(), agent_status='active')

    # AppTool.execute 内部开自己的 async_db_session.begin()（修复后）并经网关 dispatch 落库。
    result = await tool.execute(agent_ctx, {'name': name, 'description': 'commit 边界回归'})

    # 信封：app-source 工具裹 {trace_id, decision, result, ...}
    assert result.get('decision') == 'allow', f'网关未放行：{result}'
    project_id = result['result']['id']

    # 独立 session：必须看得到这一行（证明 AppTool.execute 真提交了；修前回滚→None）。
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        row = (await sess.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        # 不提交则查不到（回归：app_tools 用了无 begin 的 session）。
        assert row is not None, '项目未落库——AppTool.execute 的 session 未提交'
        assert row.name == name
    finally:
        # 清理本测试产生的 project + 其 1:1 profile（避免污染本地 dev 库）。
        await sess.execute(delete(Profile).where(Profile.project_id == project_id))
        await sess.execute(delete(Project).where(Project.id == project_id))
        await sess.commit()
        await sess.close()
        await engine.dispose()
