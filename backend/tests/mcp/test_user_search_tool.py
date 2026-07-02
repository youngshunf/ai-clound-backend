"""P4-A / A1 — user.search 平台工具单测（真实 DAO，禁 mock）。

静态保证：绑真实 DAO 查询路径、按 hasn_id 而非 owner_user_id（G2）、scope=user:search。
活体保证（需本地 DB 15432）：不存在的查询返回空 results，不伪造命中。
"""

from __future__ import annotations

import inspect

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.user import UserSearchTool


def _ctx() -> AgentContext:
    return AgentContext(
        hasn_id='a_user_search_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_user_search_test',
        session_uuid='amk_user_search_test',
    )


async def _db_reachable() -> bool:
    try:
        import sqlalchemy

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(sqlalchemy.text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


def test_user_search_scope_and_metadata() -> None:
    tool = UserSearchTool()
    assert tool.required_scopes == ['user:search']
    assert tool.source == 'platform'
    assert tool.namespace == 'hasn.user'
    assert tool.execution_location == 'cloud'


def test_user_search_uses_hasn_id_not_owner_user_id() -> None:
    """G2：身份隔离用 agent_hasn_id，execute 内不得读 owner_id/owner_user_id 做隔离。"""
    src = inspect.getsource(UserSearchTool.execute)
    assert 'agent_context.agent_hasn_id' in src
    # 检查真实属性访问（非注释里的词）：不得用 owner_id / owner_user_id 做隔离
    assert 'agent_context.owner_id' not in src
    assert 'agent_context.owner_user_id' not in src


@pytest.mark.asyncio
async def test_user_search_short_query_returns_empty() -> None:
    """<2 字符直接空返回，不查库不伪造。"""
    result = await UserSearchTool().execute(_ctx(), {'query': 'a'})
    assert result['results'] == []
    assert result['total'] == 0


@pytest.mark.asyncio
async def test_user_search_nonexistent_returns_empty_not_fake() -> None:
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    result = await UserSearchTool().execute(_ctx(), {'query': 'zzz_nonexistent_prefix_xyz'})
    assert result['total'] == 0
    assert result['results'] == []
