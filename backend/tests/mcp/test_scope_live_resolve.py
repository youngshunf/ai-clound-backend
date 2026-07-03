"""P3 — 消费时活取三态（D3）：凭证与授权解耦，toggle 即时生效。

验证：同一个已签发的 key/JWT（scopes 快照不变），改 DB 三态策略后，
不重签 key，下一次工具发现 / 执行即时反映（_can_discover / call_tool）。

用 in-memory AgentContext + 受控 policy 验证消费侧判定逻辑，
无需活体 DB（活体链路由 E2E 覆盖）。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.server import mcp_server
from backend.app.mcp.tools.contact import ContactListTool


def _ctx(default_mode: str = 'allow', capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id='a_live',
        owner_id=1,  # 快照为空——证明判定不依赖凭证上的 scopes（D3）
        agent_status='active',
        metadata={},
        owner_hasn_id='h_live',
        session_uuid='amk_live',
        default_mode=default_mode,
        capability_modes=capability_modes,
    )


def test_apply_policy_overrides_snapshot() -> None:
    ctx = _ctx()
    assert ctx.default_mode == 'allow'
    ctx.apply_policy({'default_mode': 'deny', 'capability_modes': {'contact:read': 'allow'}})
    assert ctx.default_mode == 'deny'
    assert ctx.capability_modes == {'contact:read': 'allow'}


def test_default_allow_makes_tool_discoverable_despite_empty_scopes() -> None:
    # 凭证 scopes 为空，但 default_mode='allow' → 工具可见（默认全开，BUG2 空快照根治）。
    ctx = _ctx(default_mode='allow')
    tool = ContactListTool()
    directory = mcp_server.tool_directory
    assert directory._can_discover(ctx, tool) is True
    assert mcp_server._check_tool_permission(ctx, tool) is True


def test_deny_mode_hides_and_blocks_tool_without_resigning() -> None:
    tool = ContactListTool()
    directory = mcp_server.tool_directory

    # 起初默认全开 → 可见可调
    ctx = _ctx(default_mode='allow')
    assert directory._can_discover(ctx, tool) is True

    # owner 把 contact:read 设 deny（DB 现查的新 policy），不重签 key：apply_policy 即时覆盖
    ctx.apply_policy({'default_mode': 'allow', 'capability_modes': {'contact:read': 'deny'}})
    assert directory._can_discover(ctx, tool) is False  # 即时不可见
    assert mcp_server._check_tool_permission(ctx, tool) is False  # 即时不可调


def test_ask_mode_keeps_tool_visible_and_callable_in_p3() -> None:
    # P3：ask 也可见、可调（主人批准闸门是 P6；本阶段 ask 暂等同 allow 放行）。
    tool = ContactListTool()
    directory = mcp_server.tool_directory
    ctx = _ctx(default_mode='allow', capability_modes={'contact:read': 'ask'})
    assert directory._can_discover(ctx, tool) is True
    assert mcp_server._check_tool_permission(ctx, tool) is True


@pytest.mark.asyncio
async def test_call_tool_raises_when_denied_live() -> None:
    ctx = _ctx(default_mode='allow', capability_modes={'contact:read': 'deny'})
    with pytest.raises(PermissionError, match='denied'):
        await mcp_server.call_tool(ctx, 'hasn.contact.list', {'limit': 5})
