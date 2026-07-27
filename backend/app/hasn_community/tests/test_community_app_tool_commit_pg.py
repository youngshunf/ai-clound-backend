"""community 自 commit 的 handler 经 MCP 直连面「提交边界」回归（零 mock，真实 PG :15432）。

钉死 bug：community tool handler（create_post/create_comment/like/... ）各自 ``await db.commit()``。
当 `AppTool.execute`（MCP 直连面）与 relay 面 `runtime_tool_call` 都用 ``async_db_session.begin()``
（事务上下文）调网关时，handler 的内部 commit 会在 begin() 上下文里**提前关闭事务**，随后网关
`_write_audit` 的 ``db.flush()`` 撞 “Can't operate on closed transaction inside context manager” →
community 全部自 commit 写类（发帖/评论/点赞…）经网关到达面整体报错、根本写不进。

修复：两个网关调用方（app_tools.AppTool.execute + ai_native_app.runtime_tool_call）改用**裸 session
+ 末尾显式 commit**（对齐 asset.create 模式）。裸 session 允许 handler 多次 commit，末尾 commit
再提交审计行，不再有 begin() 上下文守卫触发 closed-transaction。

本测试经**真实 AppTool.execute** 发帖，断言：①网关放行且返回 post_id（不抛 closed-transaction）；
②帖子在**独立 session**可见（=真落库）。修前红（抛异常），修后绿。需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_community.model import HasnPosts
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.app_tool_loader import load_published_app_tools
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _agent(tag: str) -> AgentTokenPayload:
    """构造与本用例真实身份行一一对应的 Agent 凭据。"""
    return AgentTokenPayload(
        agent_hasn_id=f'a_commctx_{tag}',
        agent_name='社区提交边界回归分身',
        owner_hasn_id=f'h_commctx_{tag}',
        owner_user_id=920_000_000 + int(tag, 16),
        session_uuid=f'sess-commctx-{tag}',
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


async def test_app_tool_execute_commits_community_create_post() -> None:
    """真实 AppTool.execute(hasn.community.create_post) 须放行且写入在独立 session 可见（=已提交）。"""
    if not await _pg_reachable():
        pytest.skip('本地 PostgreSQL :15432 不可达，跳过')

    tools = await load_published_app_tools()
    tool = next((t for t in tools if t.name == 'hasn.community.create_post'), None)
    assert tool is not None, 'community create_post AppTool 未在已发布工具中（builtin manifest 应含）'

    marker = uuid.uuid4().hex[:6]
    content = f'[工具测试-commctx 可忽略] community 提交边界回归 {marker}'
    token = _agent(marker)
    agent_ctx = AgentContext.from_token_payload(token, agent_status='active')

    # 先提交真实 Human/Agent 身份。IM 发卡必须走身份视图 fail-closed 校验，禁止测试合成不存在的身份。
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    post_id: str | None = None
    try:
        sess.add_all(
            [
                HasnHumans(
                    hasn_id=token.owner_hasn_id,
                    star_id=f'commctx-owner-{marker}',
                    user_id=token.owner_user_id,
                    nickname='社区提交边界主人',
                    status='active',
                ),
                HasnAgents(
                    hasn_id=token.agent_hasn_id,
                    star_id=f'commctx-agent-{marker}',
                    owner_id=token.owner_hasn_id,
                    display_name=token.agent_name,
                    agent_name='community_commit',
                    status='active',
                ),
            ]
        )
        await sess.commit()

        # handler 内部自 db.commit()；AppTool.execute 现用裸 session + 末尾 commit，不再撞 closed-transaction。
        result = await tool.execute(agent_ctx, {'content': content, 'visibility': 'private'})
        assert result.get('decision') == 'allow', f'网关未放行：{result}'
        post_id = result['result']['post_id']

        # 独立 session：必须看得到这一行（证明已提交；修前因 closed-transaction 根本到不了这）。
        row = (await sess.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))).scalar_one_or_none()
        assert row is not None, '帖子未落库——网关到达面事务未提交'
        assert row.content == content
    finally:
        if post_id is not None:
            await sess.execute(delete(HasnPosts).where(HasnPosts.post_id == post_id))
        await sess.execute(delete(HasnAgents).where(HasnAgents.hasn_id == token.agent_hasn_id))
        await sess.execute(delete(HasnHumans).where(HasnHumans.hasn_id == token.owner_hasn_id))
        await sess.commit()
        await sess.close()
        await engine.dispose()
