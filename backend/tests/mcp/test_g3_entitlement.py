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
    assert tool is not None
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

    tool = server.tool_registry.get_tool(DECK_TOOL)
    assert tool is not None
    schema = server.tool_directory._tool_schema(tool, ctx)
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


# ── 4. doc21 D-3：G3 按 reason 分派 HIDDEN / VISIBLE_DENY ──────────────────
#
# 分派依据是「主人解不解得了」：下架与灰度内测主人自己解不了 → 工具面隐身；
# 付费墙/席位/企业空间主人解得了 → 可见拒，分身要能如实转达。


@pytest.mark.parametrize('reason', ['disabled', 'need_beta', 'beta_pending'])
def test_g3_lifecycle_reasons_are_hidden(reason: str) -> None:
    """下架 / 灰度内测未获批 / 内测审核中 → HIDDEN（不再无差别 VISIBLE_DENY）。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': reason}}), _DeckTool()
    )
    assert decision.action == ACTION_HIDDEN, f'{reason} 必须隐身——主人买不到 / 申请不了'
    assert decision.gate == GATE_ENTITLEMENT
    assert decision.reason == reason
    assert decision.is_hidden
    assert not decision.is_visible_deny


@pytest.mark.parametrize(
    'reason', ['need_purchase', 'need_upgrade', 'need_seat_assignment', 'need_enterprise_space']
)
def test_g3_commercial_reasons_stay_visible_deny(reason: str) -> None:
    """商业化 reason 维持可见拒——分身能看见并如实告诉主人去买 / 升级 / 要席位 / 切空间。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': reason}}), _DeckTool()
    )
    assert decision.action == ACTION_VISIBLE_DENY
    assert decision.is_visible


def test_g3_unknown_reason_falls_back_to_visible_deny() -> None:
    """云端将来新增的未登记 reason：保守按可见拒透传原文，不静默放行也不冒充不存在。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': 'some_future_reason'}}), _DeckTool()
    )
    assert decision.action == ACTION_VISIBLE_DENY
    assert decision.reason == 'some_future_reason'


@pytest.mark.parametrize('reason', ['disabled', 'need_beta', 'beta_pending'])
def test_g3_hidden_reason_invisible_on_discovery(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    """发现面：生命周期隐身的工具不再列出（此前一律可见 + access_hint 引导购买）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': reason}})

    tool = server.tool_registry.get_tool(DECK_TOOL)
    assert tool is not None
    assert not server.tool_directory._can_discover(ctx, tool)
    # 隐身工具不带 access_hint（那是可见拒才有的引导）。
    assert 'access_hint' not in server.tool_directory._tool_schema(tool, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['disabled', 'need_beta', 'beta_pending'])
async def test_g3_hidden_reason_call_face_is_tool_not_found(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """执行面：生命周期隐身 → 与「真·未注册」逐字节同款错误（不确认存在性、不引导购买）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': reason}})

    with pytest.raises(McpToolError) as exc:
        await server.call_tool(ctx, DECK_TOOL, {})
    assert exc.value.code == McpErrorCode.TOOL_NOT_FOUND
    assert exc.value.message == f'Tool not found: {DECK_TOOL}'
    assert '购买' not in exc.value.message
    assert reason not in exc.value.message


def test_g3_hidden_does_not_hide_from_owner_permission_catalog() -> None:
    """权限页 catalog **只**消费 G1/G2 硬边界：G3 隐身不得把工具从主人的权限页抹掉。

    否则主人连「这个能力被应用状态挡住了」都看不见，复刻 102-B3 的单向门。
    """
    policy = ToolExposurePolicy()
    ctx = _ctx(app_access={'deck': {'allowed': False, 'reason': 'disabled'}})
    assert policy.evaluate(ctx, _DeckTool()).is_hidden
    assert not policy.is_catalog_hidden(ctx, _DeckTool())


def test_g3_reason_dispatch_table_matches_local_gate() -> None:
    """本地 hasn-mcp `app_gate.rs` 与云端必须共用同一张 reason 分派表（doc21 D-3）。

    两侧漂移会造成「云端说隐身、本地说可见拒」这类跨端不一致；此处把云端这一半钉死，
    本地那一半由 hasn-node 的 `app_entitlement_gate_guard` 钉死。
    """
    from backend.app.mcp.tool_exposure import LIFECYCLE_HIDDEN_REASONS

    assert LIFECYCLE_HIDDEN_REASONS == frozenset({'disabled', 'need_beta', 'beta_pending'})


# ── 5. APPDEMO-1：演示阶段应用的工具隐身（与 allowed 正交）─────────────────
#
# 前四组隐身都建立在「准入被拒」上。演示阶段不是——那些应用 allowed=True、主人照常点开看
# 原型稿，只是没有真实后端可调，所以**只**对工具面隐身。因此它必须在 not-allowed 分支之前
# 判，否则永远命不中（这正是本组测试要钉死的东西）。


def test_demo_phase_hides_tools_even_though_app_is_allowed() -> None:
    """allowed=True + tools_hidden=True → 仍然 HIDDEN。

    这是本特性的全部要害：把判定写在 `not allowed` 分支里就会**静默失效**，
    而且外观完全正常（演示应用照常可见可打开），只有分身那侧悄悄多出一批调不动的工具。
    """
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': True, 'reason': 'free', 'tools_hidden': True}}),
        _DeckTool(),
    )
    assert decision.action == ACTION_HIDDEN, '演示应用的工具必须隐身，即便应用本身准入'
    assert decision.gate == GATE_ENTITLEMENT
    assert decision.reason == 'demo_phase'


def test_demo_phase_flag_absent_or_false_keeps_tools_visible() -> None:
    """缺字段（旧云端）与显式 False 都不得隐身——never over-block。"""
    policy = ToolExposurePolicy()
    for access in ({'allowed': True, 'reason': 'free'}, {'allowed': True, 'reason': 'free', 'tools_hidden': False}):
        decision = policy.evaluate(_ctx(app_access={'deck': access}), _DeckTool())
        assert decision.action == ACTION_ALLOW, f'{access} 不该触发演示隐身'


def test_demo_phase_hidden_takes_precedence_over_commercial_deny() -> None:
    """演示 + 未购买：隐身优先于可见拒。

    演示应用没有真实后端，引导主人去买一个只有原型稿的应用是错的。
    """
    decision = ToolExposurePolicy().evaluate(
        _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase', 'tools_hidden': True}}),
        _DeckTool(),
    )
    assert decision.action == ACTION_HIDDEN
    assert decision.reason == 'demo_phase'


def test_demo_phase_invisible_on_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """发现面：演示应用的工具不列出，也不带 access_hint。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': True, 'reason': 'free', 'tools_hidden': True}})

    tool = server.tool_registry.get_tool(DECK_TOOL)
    assert tool is not None
    assert not server.tool_directory._can_discover(ctx, tool)
    assert 'access_hint' not in server.tool_directory._tool_schema(tool, ctx)


@pytest.mark.asyncio
async def test_demo_phase_call_face_is_tool_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行面：与「真·未注册」逐字节同款错误，不泄漏「这个应用在演示阶段」。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DeckTool())
    ctx = _ctx(app_access={'deck': {'allowed': True, 'reason': 'free', 'tools_hidden': True}})

    with pytest.raises(McpToolError) as exc:
        await server.call_tool(ctx, DECK_TOOL, {})
    assert exc.value.code == McpErrorCode.TOOL_NOT_FOUND
    assert exc.value.message == f'Tool not found: {DECK_TOOL}'
    assert 'demo' not in exc.value.message


def test_demo_phase_does_not_hide_from_owner_permission_catalog() -> None:
    """权限页仍列出——与 disabled 同口径，主人要能看见这个能力被应用状态挡住了。"""
    policy = ToolExposurePolicy()
    ctx = _ctx(app_access={'deck': {'allowed': True, 'reason': 'free', 'tools_hidden': True}})
    assert policy.evaluate(ctx, _DeckTool()).is_hidden
    assert not policy.is_catalog_hidden(ctx, _DeckTool())


def test_tools_hidden_only_covers_demo_phase() -> None:
    """判定源本体：只有 `demo` 隐工具，**内测两档必须不隐**。

    内测的用途就是让内测者连同分身一起真实试用；把 beta_full/beta_gray 也纳进来等于废掉内测。
    未知阶段（云端将来新增）保守按不隐，避免误伤既有应用。
    """
    from backend.app.hasn.service.app_catalog_service import tools_hidden_for_phase

    assert tools_hidden_for_phase('demo') is True
    for phase in ('ga', 'beta_full', 'beta_gray', None, '', 'some_future_phase'):
        assert tools_hidden_for_phase(phase) is False, f'{phase!r} 不该隐藏工具'
