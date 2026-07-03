"""G3 应用权益门 evaluate 纯函数 + 双面一致性验收（doc18 §4.3 · 实施/103 U3）。

判定本体零 IO——直接给 AgentContext.app_access_by_id 灌预取结果，覆盖：
1. 付费 app 未准入 → VISIBLE_DENY（可见 + reason 透传，非 HIDDEN）；
2. 已准入 / 免费（map 里 allowed=True）→ ALLOW；
3. app_id 缺席（未预取）→ 跳过 G3（never over-block）；
4. 底座工具 app_id=None → 不挂门；
5. 门顺序：G2 来源门在 G3 之前（external 未绑定仍 HIDDEN，不进 G3）；
   G3 在 G5 之前（付费墙短路，owner 三态叠加也不改 VISIBLE_DENY）；
6. 发现面 _can_discover：VISIBLE_DENY 仍可见（True），descriptor 带 access_hint；
7. 执行面 call_tool：VISIBLE_DENY → McpToolError(TOOL_NOT_ALLOWED)，reason 透传。

真实 PG 预取 + 网关 kernel 回归另见 test_g3_entitlement_pg.py。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tool_exposure import (
    ACTION_ALLOW,
    ACTION_HIDDEN,
    ACTION_VISIBLE_DENY,
    GATE_ENTITLEMENT,
    GATE_SOURCE,
    ToolExposurePolicy,
)
from backend.app.mcp.tools.base import BaseTool

# 用 deck 命名空间下的**未注册**名（namespace→app_id=deck），避开真实 hasn.deck.* 工具重名。
DECK_TOOL = 'hasn.deck.g3test'


class _DeckTool(BaseTool):
    """付费应用 stub（namespace→app_id=deck），execute 回显。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return DECK_TOOL

    @property
    def description(self) -> str:
        return 'deck create (paid app tool)'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


class _BaseMemoryTool(BaseTool):
    """底座工具 stub（namespace→app_id=None），G3 永不挂。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.memory.save'

    @property
    def description(self) -> str:
        return 'memory save (base tool)'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


class _ExternalTool(BaseTool):
    """P7 external 工具 stub：G2 来源门在 G3 之前，未绑定即 HIDDEN。"""

    @property
    def source(self) -> str:
        return 'external'

    @property
    def name(self) -> str:
        return 'hasn.ext.qcc.lookup'

    @property
    def description(self) -> str:
        return 'external qcc'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {}


def _ctx(
    *, app_access: dict | None = None, capability_modes: dict | None = None, default_mode: str = 'allow'
) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_g3_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_g3_test',
        session_uuid='amk_g3_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
    )
    ctx.app_access_by_id = app_access or {}
    return ctx


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# ── 1. evaluate 纯函数：G3 逐态 ────────────────────────────────────────────


def test_g3_paid_not_allowed_visible_deny() -> None:
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}}), _DeckTool()
    )
    assert decision.action == ACTION_VISIBLE_DENY
    assert decision.gate == GATE_ENTITLEMENT
    assert decision.reason == 'need_purchase'
    assert decision.app_id == 'deck'
    assert decision.is_visible  # 可见（带引导），不是 HIDDEN
    assert decision.is_visible_deny


def test_g3_seat_reason_passthrough() -> None:
    """reason 透传合并准入口径（need_seat_assignment / need_enterprise_space…不折叠）。"""
    for reason in ('need_seat_assignment', 'need_enterprise_space', 'need_upgrade'):
        decision = ToolExposurePolicy().evaluate(
            _ctx(app_access={'deck': {'allowed': False, 'reason': reason}}), _DeckTool()
        )
        assert decision.action == ACTION_VISIBLE_DENY
        assert decision.reason == reason


def test_g3_allowed_passes() -> None:
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': True, 'reason': 'entitled'}}), _DeckTool()
    )
    assert decision.action == ACTION_ALLOW


def test_g3_unprefetched_skips() -> None:
    """map 里没有该 app_id（未预取 / 免费未入门集合）→ 不挂门（never over-block）。"""
    decision = ToolExposurePolicy().evaluate(_ctx(app_access={}), _DeckTool())
    assert decision.action == ACTION_ALLOW


def test_g3_missing_reason_defaults_need_purchase() -> None:
    """准入 dict 没写 reason 但 allowed=False → 兜底 need_purchase（绝不静默放行付费墙）。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False}}), _DeckTool()
    )
    assert decision.action == ACTION_VISIBLE_DENY
    assert decision.reason == 'need_purchase'


def test_g3_base_tool_not_gated() -> None:
    """底座工具 app_id=None：即便 map 里有别的 app 被拒，也不挂 G3。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}}), _BaseMemoryTool()
    )
    assert decision.action == ACTION_ALLOW


# ── 2. 门顺序：G2 在 G3 前；G3 在 G5 前 ────────────────────────────────────


def test_g2_source_gate_before_g3() -> None:
    """external 未绑定：G2 短路 HIDDEN，不进 G3（哪怕 app_access 有拒绝项）。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}}), _ExternalTool()
    )
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_SOURCE  # 来源门，不是权益门


def test_g3_before_g5_owner_deny() -> None:
    """付费未准入 + owner 全局 deny：G3 在 G5 前 → 短路 VISIBLE_DENY（不被 G5 HIDDEN 盖过）。

    default_mode='deny' 令该工具的 G5 三态确为 deny——若 G3 未短路，将得 HIDDEN(owner_denied)；
    得 VISIBLE_DENY 即证 G3 在 G5 之前命中。
    """
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}}, default_mode='deny')
    # 前置断言：若单看 G5，该工具的三态确实是 deny（G3 若不短路会被 HIDDEN 盖过）
    assert ctx.tool_mode(_DeckTool()) == 'deny'
    decision = ToolExposurePolicy().evaluate(ctx, _DeckTool())
    assert decision.action == ACTION_VISIBLE_DENY  # G3 先命中，不是 owner HIDDEN


# ── 3. 双面一致性：发现面可见 + 执行面结构化错误 ──────────────────────────


def test_g3_discovery_face_visible_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """VISIBLE_DENY 工具发现面仍可见（_can_discover=True），descriptor 带 access_hint。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}})

    tool = server.tool_registry.get_tool(DECK_TOOL)
    assert server.tool_directory._can_discover(ctx, tool)  # 可见（带引导）

    schema = server.tool_directory._tool_schema(tool, ctx)
    assert schema['app_id'] == 'deck'
    assert schema['access_hint'] == {'reason': 'need_purchase', 'app_id': 'deck'}

    summary = server.tool_directory._tool_summary(tool, ctx)
    assert summary['access_hint'] == {'reason': 'need_purchase', 'app_id': 'deck'}


def test_g3_discovery_face_allowed_no_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """已准入的 descriptor 不带 access_hint（免费/已购正常工具）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': True, 'reason': 'entitled'}})

    schema = server.tool_directory._tool_schema(server.tool_registry.get_tool(DECK_TOOL), ctx)
    assert schema['app_id'] == 'deck'
    assert 'access_hint' not in schema


@pytest.mark.asyncio
async def test_g3_call_face_tool_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行面：VISIBLE_DENY → McpToolError(TOOL_NOT_ALLOWED)，文案含 app_id + reason。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}})

    with pytest.raises(McpToolError) as exc:
        await server.call_tool(ctx, DECK_TOOL, {})
    assert exc.value.code == McpErrorCode.TOOL_NOT_ALLOWED
    assert 'deck' in exc.value.message
    assert 'need_purchase' in exc.value.message


@pytest.mark.asyncio
async def test_g3_call_face_allowed_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """已准入：执行面正常放行（落到 _dispatch_by_source 真执行）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': True, 'reason': 'entitled'}})

    result = await server.call_tool(ctx, DECK_TOOL, {})
    assert result == {'executed': True}
