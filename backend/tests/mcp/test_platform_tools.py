"""P4-A — 平台工具真实 service 单测（禁 mock）。

message.send 绑 message_router.route_message（G1）：维度②对象可达性由真实路由判定，
工具返回 reachable/reason，不可达不静默成功。需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_platform_tools.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.message import MessageSendTool


def _agent_ctx() -> AgentContext:
    return AgentContext(
        hasn_id='a_platform_tool_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_platform_tool_test',
        session_uuid='amk_platform_tool_test',
    )


async def _db_reachable() -> bool:
    try:
        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(__import__('sqlalchemy').text('SELECT 1'))
        return True
    except Exception:
        return False


def test_message_send_uses_real_router_not_bare_insert() -> None:
    """静态保证 G1：工具绑 message_router（非 hasn_messages_service 裸插）。"""
    import inspect

    from backend.app.mcp.tools import message as message_module

    src = inspect.getsource(message_module.MessageSendTool.execute)
    assert 'route_message' in src, 'message.send 必须走 message_router.route_message（G1）'
    assert 'reachable' in src, 'message.send 必须返回维度②可达性'


def test_message_send_scope_renamed_to_message_send() -> None:
    """G7：message:write → message:send。"""
    assert MessageSendTool().required_scopes == ['message:send']


@pytest.mark.asyncio
async def test_message_send_unreachable_target_returns_reachable_false() -> None:
    """真实调用 route_message：目标不存在 → 维度② reachable=False，不静默成功（零 mock）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    tool = MessageSendTool()
    result = await tool.execute(_agent_ctx(), {'to': 'h_nonexistent_target_xyz', 'content': '你好'})

    assert result['reachable'] is False
    assert result['delivered'] is False
    assert result['message_id'] is None
    assert result.get('reason')  # 必须给出不可达原因
