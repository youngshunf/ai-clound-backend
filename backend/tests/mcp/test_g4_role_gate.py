"""G4 企业角色门 evaluate 纯函数 + inert 不变量守卫（doc18 §4.4 · 实施/103 U4）。

U4 现状：外部硬依赖 doc12/02「角色→能力族策略表」尚未落地（实施清单待实施），故本门
按 103 U4「未落地前本期整体后置（U1-U3/U5 不依赖）」以**结构完整但当前 inert 的接缝**落地：
- 接缝在 evaluate 里就位（工具声明 `enterprise_capability` + 企业空间 + 预取 grants 缺该族 → HIDDEN）；
- 当前 inert 有两道保险：(a) 现无内建工具声明 `enterprise_capability`；(b) `enterprise_capability_grants`
  恒为 None（策略表未解析 → 门跳过，never over-block）。

本测试既验收「接缝在 grants 灌入后判定正确」（前向可用），又用注册表全量守卫钉死「今日 inert」
（现无工具声明该字段），使策略表落地那天此守卫红灯，强制补齐工具声明 + grants 预取。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tool_exposure import (
    ACTION_ALLOW,
    ACTION_HIDDEN,
    ACTION_VISIBLE_DENY,
    GATE_ENTITLEMENT,
    GATE_ROLE,
    REASON_ROLE_INSUFFICIENT,
    ToolExposurePolicy,
)
from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    import pytest

ENT_CAP = 'oa:approve'


class _EntTool(BaseTool):
    """可配置企业能力族的 stub。默认 namespace `hasn.oa` → app_id=None（不挂 G3）。"""

    def __init__(self, *, name: str = 'hasn.oa.g4approve', enterprise_capability: str | None = None) -> None:
        self._name = name
        self._ent_cap = enterprise_capability

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return 'g4 role gate test tool'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def enterprise_capability(self) -> str | None:
        return self._ent_cap

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


def _ctx(
    *,
    active_enterprise_id: int | None = None,
    grants: frozenset[str] | None = None,
    app_access: dict | None = None,
    default_mode: str = 'allow',
    capability_modes: dict | None = None,
) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_g4_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_g4_test',
        session_uuid='amk_g4_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
    )
    ctx.active_enterprise_id = active_enterprise_id
    ctx.enterprise_capability_grants = grants
    ctx.app_access_by_id = app_access or {}
    return ctx


# ── 1. inert：默认无声明 / grants 未解析 → 门跳过（never over-block）──────────


def test_g4_undeclared_tool_never_gated() -> None:
    """工具未声明 enterprise_capability（默认 None）：即便企业空间 + 空 grants，也 ALLOW。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(active_enterprise_id=42, grants=frozenset()), _EntTool()
    )
    assert decision.action == ACTION_ALLOW


def test_g4_grants_none_is_inert() -> None:
    """策略表未落地不变量：工具声明了 cap + 企业空间，但 grants=None（未解析）→ 跳过，ALLOW。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(active_enterprise_id=42, grants=None), _EntTool(enterprise_capability=ENT_CAP)
    )
    assert decision.action == ACTION_ALLOW


def test_g4_personal_space_skips() -> None:
    """个人空间（active_enterprise_id=None）：工具声明 cap + 空 grants 也跳过 G4，ALLOW。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(active_enterprise_id=None, grants=frozenset()), _EntTool(enterprise_capability=ENT_CAP)
    )
    assert decision.action == ACTION_ALLOW


# ── 2. 接缝正确性：grants 灌入后判定（前向可用，策略表落地即生效）──────────────


def test_g4_grant_missing_hidden_role() -> None:
    """企业空间 + 工具声明 cap + grants 不含该族 → HIDDEN(role)。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(active_enterprise_id=42, grants=frozenset({'plan:manage'})),
        _EntTool(enterprise_capability=ENT_CAP),
    )
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_ROLE
    assert decision.reason == REASON_ROLE_INSUFFICIENT


def test_g4_grant_present_allows() -> None:
    """grants 含该能力族 → 过 G4，落到 G5 ALLOW。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(active_enterprise_id=42, grants=frozenset({ENT_CAP})),
        _EntTool(enterprise_capability=ENT_CAP),
    )
    assert decision.action == ACTION_ALLOW


# ── 3. 门顺序：G3 在 G4 前；G4 在 G5 前 ──────────────────────────────────────


def test_g3_before_g4() -> None:
    """付费未准入 + 企业角色也缺：G3 在 G4 前 → VISIBLE_DENY（非 HIDDEN(role)）。

    工具挂 deck 命名空间（app_id=deck，付费墙）同时声明 enterprise_capability。
    """
    tool = _EntTool(name='hasn.deck.g4approve', enterprise_capability=ENT_CAP)
    decision = ToolExposurePolicy().evaluate(
        _ctx(
            active_enterprise_id=42,
            grants=frozenset(),
            app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}},
        ),
        tool,
    )
    assert decision.action == ACTION_VISIBLE_DENY
    assert decision.gate == GATE_ENTITLEMENT  # G3 先命中，未进 G4


def test_g4_before_g5_owner_ask() -> None:
    """企业角色缺（G4 HIDDEN）+ owner 三态为 ask：G4 在 G5 前 → HIDDEN(role)，非 ASK。

    default_mode='ask' 令该工具 G5 三态为 ask——若 G4 未短路会得 ASK；得 HIDDEN(role) 即证 G4 在前。
    """
    ctx = _ctx(active_enterprise_id=42, grants=frozenset(), default_mode='ask')
    tool = _EntTool(enterprise_capability=ENT_CAP)
    # 前置断言：单看 G5 该工具确为 ask（G4 若不短路会被 ASK 盖过）
    assert ctx.tool_mode(tool) == 'ask'
    decision = ToolExposurePolicy().evaluate(ctx, tool)
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_ROLE


# ── 4. inert 不变量守卫：注册表全量无工具声明 enterprise_capability ─────────────


def test_no_builtin_tool_declares_enterprise_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """今日 inert 前提之二：现无内建工具声明 enterprise_capability（门恒不触发）。

    ⚠️ doc12/02 角色→能力族策略表落地、并给某工具声明 enterprise_capability 后，此守卫将红——
    那正是提醒：同时必须在 inject_app_access 预取主人角色能力族灌入 grants，否则门 inert 形同虚设。
    """
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)

    declared = [
        tool.name for tool in server.tool_registry.get_all_tools() if getattr(tool, 'enterprise_capability', None)
    ]
    assert declared == [], (
        f'工具 {declared} 声明了 enterprise_capability，但 G4 grants 预取尚未接线（doc12/02 未落地）——'
        '请同步在 inject_app_access 预取主人角色能力族，否则 G4 门 inert，声明形同虚设'
    )
