"""doc18 §7 回归测试矩阵（逐行）+ 全 gate 属性扫（实施/103 U5 收口验收）。

各门（G1-G5）的单门语义/双面一致已由 test_g1_privilege / test_g3_entitlement /
test_g4_role_gate / test_tool_exposure 分别锁死；本文件是**单一权威验收产物**：把 doc18 §7
矩阵十行**在同一处**用真实 server（发现面 `_can_discover` + 执行面 `call_tool`）逐行走通，
再做一次覆盖全部五门的属性扫。判定本体零 mock，工具加载/审计 no-op（同 test_tool_exposure 接缝）。

矩阵与门→执行面错误码映射（现状口径，收口决策见 §「U5 收口决策」文档）：
- G1 特权未授予 / G2 运行位置隐藏 / G4 企业角色未授予 → HIDDEN，call `TOOL_NOT_FOUND`（不确认存在性）；
- G2 external 未绑定 → HIDDEN，call `DIRECT_CALL_DENIED`（与 dispatch 兜底同码）；
- G3 应用未准入 → VISIBLE_DENY（可见带引导），call `TOOL_NOT_ALLOWED`（reason 透传）；
- G5 三态 deny → HIDDEN，call `PermissionError`（**决策项①现状保留**，见文档）；ask → 可见挂审批；allow → 放行。

属性（§7 第九行「search 可见 ⟺ call 非 TOOL_NOT_FOUND」的可严证两向 + 现状 carve-out）：
- 严证向 A：`可见 ⟹ call 不抛 TOOL_NOT_FOUND`（可见工具至少可尝试，绝不 404）；
- 严证向 B（A 的逆否）：`call 抛 TOOL_NOT_FOUND ⟹ 不可见`（存在性隐藏 ⟹ 发现面不可见）；
- carve-out：`不可见 ⟹ TOOL_NOT_FOUND` 当前**不整齐**——owner 三态 deny 走 PermissionError、
  external 未绑定走 DIRECT_CALL_DENIED（均属存在性泄漏面的**措辞差异**，收口决策项①待福仔拍板统一）。
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
    ACTION_VISIBLE_DENY,
    tool_exposure_policy,
)
from backend.app.mcp.tools.base import BaseTool

# 执行面预期结果码
CALL_EXEC = 'executes'  # 放行，execute 回显 {'executed': True}
CALL_NOT_FOUND = 'tool_not_found'  # McpToolError(TOOL_NOT_FOUND)
CALL_NOT_ALLOWED = 'tool_not_allowed'  # McpToolError(TOOL_NOT_ALLOWED)
CALL_DIRECT_DENIED = 'direct_call_denied'  # McpToolError(DIRECT_CALL_DENIED)
CALL_PERMISSION = 'permission_error'  # PermissionError（G5 deny 现状）
CALL_ASK_NOCALL = 'ask_no_call'  # ASK：可见挂审批，本测试不真调（避开审批网关 IO）


class _Stub(BaseTool):
    """全门通用 stub：按需声明 source / required_scopes / enterprise_capability，execute 仅回显。"""

    def __init__(
        self,
        name: str,
        *,
        source: str = 'platform',
        scopes: list[str] | None = None,
        enterprise_capability: str | None = None,
    ) -> None:
        self._name = name
        self._source = source
        self._scopes = scopes if scopes is not None else []
        self._ent = enterprise_capability

    @property
    def source(self) -> str:
        return self._source

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return 'doc18 matrix stub'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def required_scopes(self) -> list[str]:
        return self._scopes

    @property
    def enterprise_capability(self) -> str | None:
        return self._ent

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


def _ctx(
    *,
    granted: set[str] | None = None,
    external_allowed: set[str] | None = None,
    runtime_location: str = 'cloud',
    capability_modes: dict | None = None,
    app_access: dict | None = None,
    active_enterprise_id: int | None = None,
    enterprise_capability_grants: frozenset[str] | None = None,
) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_matrix',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_matrix',
        session_uuid='amk_matrix',
        capability_modes=capability_modes or {},
        runtime_location=runtime_location,
    )
    ctx.granted_privileged_scopes = frozenset(granted or set())
    ctx.external_allowed_tools = external_allowed or set()
    ctx.app_access_by_id = app_access or {}
    ctx.active_enterprise_id = active_enterprise_id
    ctx.enterprise_capability_grants = enterprise_capability_grants
    return ctx


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# 每行：(doc18 §7 行标签, tool, ctx, 期望 action, 期望发现面可见, 期望执行面结果码)
def _matrix() -> list[tuple[str, _Stub, AgentContext, str, bool, str]]:
    return [
        # 行1：普通分身 tool.search 搜不到 hasn.diag.*（G1 HIDDEN），search/call 均 TOOL_NOT_FOUND
        (
            '普通分身·特权工具不可见',
            _Stub('hasn.diag.mun', scopes=['diag:read:all']),
            _ctx(),
            ACTION_HIDDEN,
            False,
            CALL_NOT_FOUND,
        ),
        # 行2：运维分身（表授予后）可见可调
        (
            '运维分身·授予后可见可调',
            _Stub('hasn.diag.mgr', scopes=['diag:read:all']),
            _ctx(granted={'diag:read:all'}),
            ACTION_ALLOW,
            True,
            CALL_EXEC,
        ),
        # 行2'：owner 对运维分身设 deny → G5 叠加收紧（G5 在 G1 之后，只收紧）
        (
            '运维分身·owner deny 叠加收紧',
            _Stub('hasn.diag.mdeny', scopes=['diag:read:all']),
            _ctx(granted={'diag:read:all'}, capability_modes={'diag:read:all': 'deny'}),
            ACTION_HIDDEN,
            False,
            CALL_PERMISSION,
        ),
        # 行5：企业空间·企业未购应用 → 可见带 need_purchase，call 被拒（G3 VISIBLE_DENY）
        (
            '企业未购·可见拒 need_purchase',
            _Stub('hasn.deck.mnp', scopes=['deck:write']),
            _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_purchase'}}),
            ACTION_VISIBLE_DENY,
            True,
            CALL_NOT_ALLOWED,
        ),
        # 行6：企业空间·已购未派席位成员 → need_seat_assignment，call 被拒
        (
            '已购未派席位·可见拒 need_seat_assignment',
            _Stub('hasn.deck.mseat', scopes=['deck:write']),
            _ctx(app_access={'deck': {'allowed': False, 'reason': 'need_seat_assignment'}}),
            ACTION_VISIBLE_DENY,
            True,
            CALL_NOT_ALLOWED,
        ),
        # 行7：个人空间·个人已购 → 正常（G3 走 owner 权益）
        (
            '个人已购·正常放行',
            _Stub('hasn.deck.mok', scopes=['deck:write']),
            _ctx(app_access={'deck': {'allowed': True}}),
            ACTION_ALLOW,
            True,
            CALL_EXEC,
        ),
        # 行8：企业空间·成员角色无 enterprise_capability → 不可见（G4 HIDDEN）
        (
            '企业角色不足·工具不可见',
            _Stub('hasn.oa.mrole', enterprise_capability='oa:approve'),
            _ctx(active_enterprise_id=42, enterprise_capability_grants=frozenset()),
            ACTION_HIDDEN,
            False,
            CALL_NOT_FOUND,
        ),
        # 行8'：主人角色提升后即时可见（G4 grants 含该能力族）
        (
            '企业角色提升·即时可见',
            _Stub('hasn.oa.mrok', enterprise_capability='oa:approve'),
            _ctx(active_enterprise_id=42, enterprise_capability_grants=frozenset({'oa:approve'})),
            ACTION_ALLOW,
            True,
            CALL_EXEC,
        ),
        # 现状回归：external 未绑定不可见 + DIRECT_CALL_DENIED
        (
            'external 未绑定·不可见',
            _Stub('hasn.ext.srv.unbound', source='external'),
            _ctx(),
            ACTION_HIDDEN,
            False,
            CALL_DIRECT_DENIED,
        ),
        (
            'external 已绑定·可见可调',
            _Stub('hasn.ext.srv.bound', source='external'),
            _ctx(external_allowed={'hasn.ext.srv.bound'}),
            ACTION_ALLOW,
            True,
            CALL_EXEC,
        ),
        # 现状回归：runtime 面隐藏（本地分身云端面看不到 deck/task/workflow）
        (
            'runtime 本地隐藏·不可见',
            _Stub('hasn.deck.mrt', scopes=['deck:write']),
            _ctx(runtime_location='local'),
            ACTION_HIDDEN,
            False,
            CALL_NOT_FOUND,
        ),
        # 现状回归：三态 deny / ask / allow
        (
            '三态 deny·不可见',
            _Stub('hasn.stub.mden', scopes=['stub:mden']),
            _ctx(capability_modes={'stub:mden': 'deny'}),
            ACTION_HIDDEN,
            False,
            CALL_PERMISSION,
        ),
        (
            '三态 ask·可见挂审批',
            _Stub('hasn.stub.mask', scopes=['stub:mask']),
            _ctx(capability_modes={'stub:mask': 'ask'}),
            ACTION_ASK,
            True,
            CALL_ASK_NOCALL,
        ),
        (
            '三态 allow·放行',
            _Stub('hasn.stub.mallow', scopes=['stub:mallow']),
            _ctx(),
            ACTION_ALLOW,
            True,
            CALL_EXEC,
        ),
    ]


async def _assert_call_outcome(
    server: HasnCloudMcpServer, ctx: AgentContext, name: str, expected: str
) -> str | None:
    """真调执行面并断言结果码；返回实际观察到的「执行面是否 TOOL_NOT_FOUND」标记供属性检查。

    ASK 不真调（避开审批网关轮询 IO），返回 None。
    """
    if expected == CALL_ASK_NOCALL:
        return None
    if expected == CALL_EXEC:
        result = await server.call_tool(ctx, name, {})
        assert result == {'executed': True}, name
        return CALL_EXEC
    if expected == CALL_PERMISSION:
        with pytest.raises(PermissionError):
            await server.call_tool(ctx, name, {})
        return CALL_PERMISSION
    # 其余三态都抛 McpToolError，按码区分
    code_by_expected = {
        CALL_NOT_FOUND: McpErrorCode.TOOL_NOT_FOUND,
        CALL_NOT_ALLOWED: McpErrorCode.TOOL_NOT_ALLOWED,
        CALL_DIRECT_DENIED: McpErrorCode.DIRECT_CALL_DENIED,
    }
    with pytest.raises(McpToolError) as exc:
        await server.call_tool(ctx, name, {})
    assert exc.value.code == code_by_expected[expected], f'{name}: {exc.value.code}'
    return expected


@pytest.mark.asyncio
async def test_doc18_section7_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    """doc18 §7 矩阵逐行：每行 evaluate action + 发现面可见性 + 执行面结果码全部对齐。"""
    server = _server_with_noop_io(monkeypatch)
    rows = _matrix()
    for _, tool, _ctx_unused, _, _, _ in rows:
        server.tool_registry.register(tool)

    for label, tool, ctx, expected_action, expected_visible, expected_call in rows:
        decision = tool_exposure_policy.evaluate(ctx, tool)
        assert decision.action == expected_action, f'{label}: action={decision.action}'
        assert server.tool_directory._can_discover(ctx, tool) is expected_visible, f'{label}: 发现面'
        await _assert_call_outcome(server, ctx, tool.name, expected_call)


@pytest.mark.asyncio
async def test_property_visible_iff_not_tool_not_found_full_gate_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """§7 第九行属性（全五门扫）：可见 ⟹ call 非 TOOL_NOT_FOUND；call TOOL_NOT_FOUND ⟹ 不可见。

    对矩阵每行断言两个严证向；ASK 行结构上必可见且不落 TOOL_NOT_FOUND（不真调）。
    覆盖 G1/G2(external+runtime)/G3/G4/G5 全部门的 HIDDEN、VISIBLE_DENY、ASK、ALLOW 出口。
    """
    server = _server_with_noop_io(monkeypatch)
    rows = _matrix()
    for _, tool, _ctx_unused, _, _, _ in rows:
        server.tool_registry.register(tool)

    seen_hidden = seen_visible_deny = seen_ask = seen_allow = 0
    for label, tool, ctx, expected_action, _, _ in rows:
        visible = server.tool_directory._can_discover(ctx, tool)
        # ASK 不真调，仅锁「可见 + 结构上非 404」
        if expected_action == ACTION_ASK:
            assert visible, f'{label}: ASK 必可见'
            seen_ask += 1
            continue
        observed = await _assert_call_outcome(server, ctx, tool.name, _expected_call_for(rows, label))
        is_not_found = observed == CALL_NOT_FOUND
        # 严证向 A：可见 ⟹ 非 TOOL_NOT_FOUND
        if visible:
            assert not is_not_found, f'{label}: 可见工具执行面不应 TOOL_NOT_FOUND'
        # 严证向 B：TOOL_NOT_FOUND ⟹ 不可见
        if is_not_found:
            assert not visible, f'{label}: TOOL_NOT_FOUND 工具不应发现面可见（存在性泄漏）'
        seen_hidden += int(expected_action == ACTION_HIDDEN)
        seen_visible_deny += int(expected_action == ACTION_VISIBLE_DENY)
        seen_allow += int(expected_action == ACTION_ALLOW)

    # 覆盖度：四类出口都被扫到（属性扫非空转）
    assert seen_hidden >= 4  # G1/G2runtime/G4/G5deny/external 多门 HIDDEN
    assert seen_visible_deny >= 1
    assert seen_ask >= 1
    assert seen_allow >= 3


def _expected_call_for(rows: list[tuple[str, _Stub, AgentContext, str, bool, str]], label: str) -> str:
    for row_label, _, _, _, _, expected_call in rows:
        if row_label == label:
            return expected_call
    raise AssertionError(label)


def test_discovery_equals_evaluate_projection_over_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    """现状回归（§7 末行·四面同源）：发现面 = evaluate 非 HIDDEN 的投影，逐行核对。"""
    server = _server_with_noop_io(monkeypatch)
    for label, tool, ctx, _, _, _ in _matrix():
        server.tool_registry.register(tool)
        visible = server.tool_directory._can_discover(ctx, tool)
        assert visible == tool_exposure_policy.evaluate(ctx, tool).is_visible, label
