"""统一工具暴露管线 ToolExposurePolicy（doc18 §3 · 实施/103 U1）。

U1 = 零行为变化收编：G2（external 白名单 + runtime 命名空间隐藏）与 G5（owner
三态）从三处散落判定收敛为单一 evaluate，三面消费同一投影。本文件锁两层：

1. evaluate 纯函数语义（gate/reason/action 逐门断言，零 IO 零 DB）；
2. 面一致性属性（doc18 §7 一致性行）：同一 ctx 下遍历真实注册表，
   `search 可见 ⟺ evaluate 非 HIDDEN`，且执行面对 HIDDEN 的错误映射与收编前
   逐位一致（runtime 隐藏→TOOL_NOT_FOUND；三态 deny→PermissionError——
   「deny 执行面也归 TOOL_NOT_FOUND」属行为变化，留 U5 拍板，此处锁现状）。

工具加载/审计落库被 no-op（与 test_capability_ticket 同款接缝），判定本体零 mock。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tool_exposure import (
    ACTION_ALLOW,
    ACTION_ASK,
    ACTION_HIDDEN,
    GATE_OWNER,
    GATE_SOURCE,
    REASON_EXTERNAL_NOT_BOUND,
    REASON_OWNER_DENIED,
    REASON_RUNTIME_HIDDEN,
    ToolExposurePolicy,
    tool_exposure_policy,
)
from backend.app.mcp.tools.base import BaseTool


class _StubTool(BaseTool):
    """平台 stub 工具（不执行任何 IO，execute 仅回显以证「放行才执行」）。"""

    def __init__(self, name: str = 'hasn.stub.act', source: str = 'platform', scopes: list[str] | None = None) -> None:
        self._name = name
        self._source = source
        self._scopes = scopes if scopes is not None else ['stub:act']

    @property
    def source(self) -> str:
        return self._source

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return 'exposure pipeline stub'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def required_scopes(self) -> list[str]:
        return self._scopes

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


def _ctx(
    *,
    runtime_location: str = 'cloud',
    default_mode: str = 'allow',
    capability_modes: dict | None = None,
    external_allowed: set[str] | None = None,
) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_exposure_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_exposure_test',
        session_uuid='amk_exposure_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
        runtime_location=runtime_location,
    )
    ctx.external_allowed_tools = external_allowed or set()
    return ctx


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# ── 1. evaluate 纯函数语义（逐门） ─────────────────────────────────────────


def test_g2_external_not_bound_hidden_and_bound_allowed() -> None:
    policy = ToolExposurePolicy()
    ext = _StubTool(name='hasn.ext.srv.echo', source='external', scopes=[])

    unbound = policy.evaluate(_ctx(), ext)
    assert unbound.action == ACTION_HIDDEN
    assert unbound.gate == GATE_SOURCE
    assert unbound.reason == REASON_EXTERNAL_NOT_BOUND

    bound = policy.evaluate(_ctx(external_allowed={'hasn.ext.srv.echo'}), ext)
    assert bound.action == ACTION_ALLOW


def test_g2_runtime_namespace_hidden_only_for_local_agent() -> None:
    policy = ToolExposurePolicy()
    deck = _StubTool(name='hasn.deck.create', scopes=['deck:write'])

    local = policy.evaluate(_ctx(runtime_location='local'), deck)
    assert local.action == ACTION_HIDDEN
    assert local.gate == GATE_SOURCE
    assert local.reason == REASON_RUNTIME_HIDDEN

    # 空 / 未知 / cloud / remote 一律可见（绝不误伤云端分身）
    for loc in ('cloud', 'remote', '', 'unknown'):
        assert policy.evaluate(_ctx(runtime_location=loc), deck).action == ACTION_ALLOW


def test_g5_tristate_deny_ask_allow() -> None:
    policy = ToolExposurePolicy()
    tool = _StubTool()

    denied = policy.evaluate(_ctx(capability_modes={'stub:act': 'deny'}), tool)
    assert denied.action == ACTION_HIDDEN
    assert denied.gate == GATE_OWNER
    assert denied.reason == REASON_OWNER_DENIED

    asked = policy.evaluate(_ctx(capability_modes={'stub:act': 'ask'}), tool)
    assert asked.action == ACTION_ASK
    assert asked.gate == GATE_OWNER

    assert policy.evaluate(_ctx(), tool).action == ACTION_ALLOW


def test_g5_never_loosens_earlier_gates() -> None:
    """三态是 owner 态度层：allow 也放行不了 G2 硬边界（doc18 §3.2 顺序即优先级）。"""
    policy = ToolExposurePolicy()
    ext = _StubTool(name='hasn.ext.srv.echo', source='external', scopes=[])
    decision = policy.evaluate(_ctx(capability_modes={'hasn.ext.srv.echo': 'allow'}), ext)
    assert decision.action == ACTION_HIDDEN
    assert decision.reason == REASON_EXTERNAL_NOT_BOUND


# ── 2. 面一致性属性（真实注册表 · doc18 §7 一致性行） ─────────────────────


def test_discovery_face_equals_evaluate_projection() -> None:
    """发现面 = evaluate 非 HIDDEN 的投影：遍历真实注册表逐工具锁接缝。"""
    server = HasnCloudMcpServer()
    ctx = _ctx()
    tools = server.tool_registry.get_all_tools()
    assert tools, '真实注册表不应为空'
    for tool in tools:
        visible = server.tool_directory._can_discover(ctx, tool)
        assert visible == tool_exposure_policy.evaluate(ctx, tool).is_visible, tool.name


@pytest.mark.asyncio
async def test_local_agent_runtime_hidden_consistent_across_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    """属性：本地分身 ctx 下 `search 可见 ⟺ call 非 TOOL_NOT_FOUND`（可判定半侧）。

    对 evaluate=HIDDEN(runtime_hidden) 的每个真实注册工具，断言发现面不可见且
    执行面抛 TOOL_NOT_FOUND（错误码与收编前逐位一致）；可见侧不真调用（会执行
    工具体），由 test_discovery_face_equals_evaluate_projection 锁投影等式。
    """
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx(runtime_location='local')

    hidden_names: set[str] = set()
    for tool in server.tool_registry.get_all_tools():
        decision = tool_exposure_policy.evaluate(ctx, tool)
        if not decision.is_hidden:
            continue
        assert decision.reason == REASON_RUNTIME_HIDDEN, tool.name
        hidden_names.add(tool.name)
        # 发现面不可见
        assert not server.tool_directory._can_discover(ctx, tool)
        # 执行面 TOOL_NOT_FOUND（不确认存在性）
        with pytest.raises(McpToolError) as exc_info:
            await server.call_tool(ctx, tool.name, {})
        assert exc_info.value.code == McpErrorCode.TOOL_NOT_FOUND

    # deck/task/workflow 三域确实被隐藏（TOOLMIG2-P4 现状锁定）
    assert any(name.startswith('hasn.deck.') for name in hidden_names)
    assert any(name.startswith('hasn.task.') for name in hidden_names)
    assert any(name.startswith('hasn.workflow.') for name in hidden_names)
    # 云端分身零隐藏
    cloud_ctx = _ctx()
    for tool in server.tool_registry.get_all_tools():
        assert tool_exposure_policy.evaluate(cloud_ctx, tool).is_visible, tool.name


@pytest.mark.asyncio
async def test_owner_denied_hidden_in_search_but_permission_error_on_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """现状锁定（U5 拍板项）：三态 deny 发现面隐身、执行面 PermissionError（非 TOOL_NOT_FOUND）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_StubTool())
    ctx = _ctx(capability_modes={'stub:act': 'deny'})

    tool = server.tool_registry.get_tool('hasn.stub.act')
    assert not server.tool_directory._can_discover(ctx, tool)
    with pytest.raises(PermissionError, match='Capability denied by owner'):
        await server.call_tool(ctx, 'hasn.stub.act', {})


@pytest.mark.asyncio
async def test_external_unbound_hidden_in_search_and_direct_call_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """external 未绑定：发现面隐身；执行面 DIRECT_CALL_DENIED（与收编前 dispatch 兜底同码同文案）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_StubTool(name='hasn.ext.srv.echo', source='external', scopes=[]))

    ctx = _ctx()
    tool = server.tool_registry.get_tool('hasn.ext.srv.echo')
    assert not server.tool_directory._can_discover(ctx, tool)
    with pytest.raises(McpToolError) as exc_info:
        await server.call_tool(ctx, 'hasn.ext.srv.echo', {})
    assert exc_info.value.code == McpErrorCode.DIRECT_CALL_DENIED

    # 绑定后可见可调（execute 为纯回显 stub）
    bound_ctx = _ctx(external_allowed={'hasn.ext.srv.echo'})
    assert server.tool_directory._can_discover(bound_ctx, tool)
    result = await server.call_tool(bound_ctx, 'hasn.ext.srv.echo', {})
    assert result == {'executed': True}


def test_ask_mode_visible_in_discovery() -> None:
    """ask 仍可见（调用时挂审批）：发现面不因 ASK 隐藏。审批链路本体见 test_capability_ticket。"""
    server = HasnCloudMcpServer()
    server.tool_registry.register(_StubTool())
    ctx = _ctx(capability_modes={'stub:act': 'ask'})
    tool = server.tool_registry.get_tool('hasn.stub.act')
    assert tool_exposure_policy.evaluate(ctx, tool).action == ACTION_ASK
    assert server.tool_directory._can_discover(ctx, tool)
