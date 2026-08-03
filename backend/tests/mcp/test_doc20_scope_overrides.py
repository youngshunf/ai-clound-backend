"""派发级能力域收紧（doc20-tools D-2）的云端消费方验收。

**这补的是一个跨仓真缺口**：`hasn.task.run_now` 声明 `task:run`，而工作会话默认 deny
`task:run`；此前云端 MCP 面**没有任何 `scope_overrides` 消费方**，分身在工作会话里经 cloud
MCP 调 `hasn.task.run_now` 仍能触发派发——防自激循环整条被绕过。

判定语义照抄 hasn-node `crates/hasn-node/src/session_scope.rs`，**不另立一套**：

- 最终生效值 = `min(Agent 三态, 派发覆盖)`，保守序 `deny > ask > allow`；
- 覆盖值只允许 `ask` / `deny`；`allow` 是「放宽」，本模型里不存在 → 契约错误；
- 键匹配用工具的 `required_scopes`（与 `capability_guard` 消费 `capability_modes` 同一套 key）；
- 非法输入拒绝调用，**不静默忽略**（doc18 §10.2 B7）。

覆盖面：① wire 契约常量；② 严格解析与剥离；③ 保守合并；④ 只收紧不放宽；
⑤ **发现面与执行面同源**（doc18 §2 D6）；⑥ G5 内收紧不放行 G1–G4 任何一门；
⑦ 主人权限页 catalog 不被 per-dispatch 收紧污染。
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
    REASON_OWNER_DENIED,
    ToolExposurePolicy,
)
from backend.app.mcp.tools.base import BaseTool
from backend.app.mcp.trust_gate import (
    RESERVED_SCOPE_OVERRIDES,
    SCOPE_OVERRIDE_MODES,
    narrow_mode_with_scope_overrides,
    narrowed_scope_overrides,
    parse_scope_overrides,
    pop_scope_overrides,
)

# 真实的云端派发触发工具：本轨要堵的就是它（`required_scopes == ['task:run']`）。
RUN_NOW_TOOL = 'hasn.task.run_now'
SCOPE_TASK_RUN = 'task:run'
# 工作会话的出厂默认收紧（hasn-node `default_scope_overrides(SessionShape::WorkSession)` 的 wire 形态）。
WORK_SESSION_OVERRIDES = {SCOPE_TASK_RUN: 'deny'}


class _ExternalTool(BaseTool):
    """P7 external 工具 stub：G2 来源门在 G5 之前，未绑定即 HIDDEN。"""

    @property
    def source(self) -> str:
        return 'external'

    @property
    def name(self) -> str:
        return 'hasn.ext.doc20.dispatch'

    @property
    def description(self) -> str:
        return 'external dispatch-ish tool'

    @property
    def required_scopes(self) -> list[str]:
        return [SCOPE_TASK_RUN]

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {}


def _ctx(*, scope_overrides: dict[str, str] | None = None, capability_modes: dict | None = None) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_doc20_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_doc20_test',
        session_uuid='amk_doc20_test',
        capability_modes=capability_modes or {},
    )
    ctx.scope_overrides = dict(scope_overrides or {})
    return ctx


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    """真实注册表 + 真实五门管线，只掐掉 DB/网络 IO（零 mock 判定逻辑）。"""
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# ── ① 跨仓 wire 契约（改一个字节两侧就合不上）────────────────────────────────


def test_wire_contract_matches_hasn_node() -> None:
    """键名与取值集合是跨仓契约。

    对照事实源：`crates/hasn-mcp/src/auth.rs::RESERVED_SCOPE_OVERRIDES_ARG`、
    `crates/hasn-node/src/session_scope.rs::mode_wire_value`、
    hermes fork `gateway/hasn_session.py::RESERVED_SCOPE_OVERRIDES_ARG`。
    """
    assert RESERVED_SCOPE_OVERRIDES == '_hasn_scope_overrides'
    assert SCOPE_OVERRIDE_MODES == frozenset({'ask', 'deny'})


def test_run_now_still_declares_task_run_scope() -> None:
    """本轨的门禁全靠 `hasn.task.run_now` 声明 `task:run`——它一改名，收紧就静默失效。"""
    tool = HasnCloudMcpServer().tool_registry.get_tool(RUN_NOW_TOOL)
    assert tool is not None
    assert tool.required_scopes == [SCOPE_TASK_RUN]


# ── ② 严格解析：非法即拒，绝不静默忽略 ──────────────────────────────────────


def test_parse_accepts_ask_and_deny() -> None:
    assert parse_scope_overrides({}) == {}
    assert parse_scope_overrides({'task:run': 'deny', 'media:generate': 'ask'}) == {
        'task:run': 'deny',
        'media:generate': 'ask',
    }


def test_parse_rejects_allow_as_widening() -> None:
    """`allow` 是「放宽」，而放宽在本模型里不存在——必须是契约错误，不是忽略。"""
    with pytest.raises(McpToolError) as exc:
        parse_scope_overrides({SCOPE_TASK_RUN: 'allow'})
    assert exc.value.code == McpErrorCode.TOOL_NOT_ALLOWED
    assert '只能收紧' in exc.value.message


@pytest.mark.parametrize(
    'invalid',
    [
        None,
        'task:run=deny',
        [['task:run', 'deny']],
        {'': 'deny'},
        {'   ': 'deny'},
        {'task run': 'deny'},
        {'task:run\n': 'deny'},
        {1: 'deny'},
        {'task:run': 'Deny'},
        {'task:run': ' deny'},
        {'task:run': 'maybe'},
        {'task:run': True},
        {'task:run': None},
    ],
)
def test_parse_rejects_invalid_wire(invalid: object) -> None:
    with pytest.raises(McpToolError) as exc:
        parse_scope_overrides(invalid)
    assert exc.value.code == McpErrorCode.TOOL_NOT_ALLOWED


def test_parse_accepts_scopes_unknown_to_cloud() -> None:
    """本地独有 scope（`plan:schedule` 等）必须放行——Runtime 对 local/cloud 盖的是同一份覆盖。

    在云端按云端 scope 目录判「未知即拒」会把完全合法的派发整条打死；未知键在云端只是
    匹配不到任何 `required_scopes`，自然无收紧效果。「未知键拒绝派发」由 daemon 的
    `validate_against_known` 在下达端负责（它才持有本地 scope 全集）。
    """
    assert parse_scope_overrides({'plan:schedule': 'deny'}) == {'plan:schedule': 'deny'}


# ── ③ 剥离与合并 ────────────────────────────────────────────────────────────


def test_pop_strips_reserved_arg_and_keeps_the_rest() -> None:
    arguments, overrides = pop_scope_overrides({
        'task_id': 't-1',
        RESERVED_SCOPE_OVERRIDES: WORK_SESSION_OVERRIDES,
    })
    assert arguments == {'task_id': 't-1'}
    assert overrides == WORK_SESSION_OVERRIDES


def test_pop_without_reserved_arg_returns_none() -> None:
    """缺省零回归：没盖章时原样返回 + None（重入的内层 tool.call 走的正是这条）。"""
    clean = {'task_id': 't-1'}
    arguments, overrides = pop_scope_overrides(clean)
    assert arguments is clean
    assert overrides is None


def test_merge_takes_the_narrower_and_is_idempotent() -> None:
    """同键取更严、异键取并集；重复叠加不漂移（与 `ScopeOverrides::narrowed_with` 同款）。"""
    merged = narrowed_scope_overrides(WORK_SESSION_OVERRIDES, {SCOPE_TASK_RUN: 'ask'})
    assert merged == {SCOPE_TASK_RUN: 'deny'}  # 想放松成 ask，合并后仍是 deny
    assert narrowed_scope_overrides(merged, WORK_SESSION_OVERRIDES) == merged  # 幂等
    assert narrowed_scope_overrides(merged, {'media:generate': 'ask'}) == {
        SCOPE_TASK_RUN: 'deny',
        'media:generate': 'ask',
    }
    assert narrowed_scope_overrides(None, WORK_SESSION_OVERRIDES) == WORK_SESSION_OVERRIDES


# ── ④ 判定语义：只收紧，绝不放宽 ────────────────────────────────────────────


def test_narrow_never_widens_owner_deny() -> None:
    scopes = [SCOPE_TASK_RUN]
    # 主人 deny → 派发侧配 ask 也仍是 deny。
    assert narrow_mode_with_scope_overrides('deny', {SCOPE_TASK_RUN: 'ask'}, scopes) == 'deny'
    # 主人 allow → 派发侧 ask / deny 收紧生效。
    assert narrow_mode_with_scope_overrides('allow', {SCOPE_TASK_RUN: 'ask'}, scopes) == 'ask'
    assert narrow_mode_with_scope_overrides('allow', WORK_SESSION_OVERRIDES, scopes) == 'deny'
    # 未列出的能力域保持 Agent 既有三态。
    assert narrow_mode_with_scope_overrides('allow', WORK_SESSION_OVERRIDES, ['media:generate']) == 'allow'
    # 无 scope 声明的读类工具不受影响。
    assert narrow_mode_with_scope_overrides('allow', WORK_SESSION_OVERRIDES, []) == 'allow'
    # 空覆盖 = 不收紧。
    assert narrow_mode_with_scope_overrides('allow', {}, scopes) == 'allow'


def test_narrow_multi_scope_tool_takes_the_strictest() -> None:
    """任一能力域被收紧即对整个工具生效（与 `resolve_tool_mode` 的聚合语义一致）。"""
    overrides = {'task:run': 'ask', 'task:manage': 'deny'}
    assert narrow_mode_with_scope_overrides('allow', overrides, ['task:run', 'task:manage']) == 'deny'


def test_agent_context_tool_mode_applies_the_narrowing() -> None:
    """真实工具 + 真实 CapabilityGuard：三态 allow 的 `run_now` 在工作会话里变 deny。"""
    tool = HasnCloudMcpServer().tool_registry.get_tool(RUN_NOW_TOOL)
    assert tool is not None
    assert _ctx().tool_mode(tool) == 'allow'  # 前置：不收紧时确实是 allow
    assert _ctx(scope_overrides=WORK_SESSION_OVERRIDES).tool_mode(tool) == 'deny'
    assert _ctx(scope_overrides={SCOPE_TASK_RUN: 'ask'}).tool_mode(tool) == 'ask'


def test_agent_context_tool_mode_leaves_other_tools_alone() -> None:
    """收紧只落在声明了该能力域的工具上，不误伤同 app 的读类工具（`hasn.task.list` 无 scope）。"""
    tool = HasnCloudMcpServer().tool_registry.get_tool('hasn.task.list')
    assert tool is not None
    assert tool.required_scopes == []
    assert _ctx(scope_overrides=WORK_SESSION_OVERRIDES).tool_mode(tool) == 'allow'


# ── ⑤ 发现面与执行面同源（doc18 §2 D6）──────────────────────────────────────


def test_discovery_and_call_faces_agree_under_narrowing(monkeypatch: pytest.MonkeyPatch) -> None:
    """两面共用 `ToolExposurePolicy.evaluate` → `AgentContext.tool_mode`，结论必须一致。

    不能出现「搜得到调不动」或「搜不到却能调」：这里同时断言发现面 `_can_discover`
    与执行面 `evaluate` 的决策，且两者都来自同一个 evaluate。
    """
    server = _server_with_noop_io(monkeypatch)
    tool = server.tool_registry.get_tool(RUN_NOW_TOOL)
    assert tool is not None

    # 不收紧（主会话）：两面都放行。
    open_ctx = _ctx()
    assert server.tool_directory._can_discover(open_ctx, tool)
    assert ToolExposurePolicy().evaluate(open_ctx, tool).action == ACTION_ALLOW

    # 工作会话默认收紧：发现面隐身 + 执行面判 HIDDEN(owner_denied)。
    narrowed_ctx = _ctx(scope_overrides=WORK_SESSION_OVERRIDES)
    assert not server.tool_directory._can_discover(narrowed_ctx, tool)
    decision = ToolExposurePolicy().evaluate(narrowed_ctx, tool)
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_OWNER
    assert decision.reason == REASON_OWNER_DENIED

    # 覆盖成 ask：两面都可见，执行面挂审批（这是白名单/黑名单都表达不了的第三态）。
    ask_ctx = _ctx(scope_overrides={SCOPE_TASK_RUN: 'ask'})
    assert server.tool_directory._can_discover(ask_ctx, tool)
    assert ToolExposurePolicy().evaluate(ask_ctx, tool).action == ACTION_ASK


def test_search_face_hides_narrowed_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """发现面的真实投影：收紧后 `hasn.task.run_now` 不再出现在工具清单里。"""
    server = _server_with_noop_io(monkeypatch)

    open_names = {tool['name'] for tool in server.tool_directory.list_all_tools(_ctx())}
    assert RUN_NOW_TOOL in open_names

    narrowed_names = {
        tool['name'] for tool in server.tool_directory.list_all_tools(_ctx(scope_overrides=WORK_SESSION_OVERRIDES))
    }
    assert RUN_NOW_TOOL not in narrowed_names
    # 只掉了声明 `task:run` 的那些，其余工具面零回归。
    assert open_names - narrowed_names == {RUN_NOW_TOOL}


@pytest.mark.asyncio
async def test_call_face_rejects_narrowed_dispatch_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行面：工作会话里调 `hasn.task.run_now` 必须在 dispatch 前被拒（防自激循环）。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx(scope_overrides=WORK_SESSION_OVERRIDES)

    with pytest.raises(PermissionError):
        await server.call_tool(ctx, RUN_NOW_TOOL, {'task_id': 't-1'})


@pytest.mark.asyncio
async def test_call_face_consumes_stamped_reserved_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：Runtime 盖章的保留参数被真正消费——同一次调用即被拒，无需预置上下文。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx()

    with pytest.raises(PermissionError):
        await server.call_tool(
            ctx,
            RUN_NOW_TOOL,
            {'task_id': 't-1', RESERVED_SCOPE_OVERRIDES: WORK_SESSION_OVERRIDES},
        )
    # 剥离后落进上下文（工具体永不该见到保留参数）。
    assert ctx.scope_overrides == WORK_SESSION_OVERRIDES


@pytest.mark.asyncio
async def test_call_face_rejects_invalid_stamped_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法覆盖必须拒绝调用而不是静默忽略——静默忽略等于本次派发的门禁整条失效。"""
    server = _server_with_noop_io(monkeypatch)

    for invalid in ({SCOPE_TASK_RUN: 'allow'}, {SCOPE_TASK_RUN: 'maybe'}, 'task:run=deny'):
        with pytest.raises(McpToolError) as exc:
            await server.call_tool(_ctx(), 'hasn.task.list', {RESERVED_SCOPE_OVERRIDES: invalid})
        assert exc.value.code == McpErrorCode.TOOL_NOT_ALLOWED


@pytest.mark.asyncio
async def test_reentrant_tool_call_keeps_outer_narrowing(monkeypatch: pytest.MonkeyPatch) -> None:
    """转发面 `hasn.cloud.tool.call` 重入时内层入参没有 stamp，必须沿用外层收紧（只进不退）。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx()

    with pytest.raises(PermissionError):
        await server.call_tool(
            ctx,
            'hasn.cloud.tool.call',
            {
                'name': RUN_NOW_TOOL,
                'params': {'task_id': 't-1'},
                RESERVED_SCOPE_OVERRIDES: WORK_SESSION_OVERRIDES,
            },
        )


# ── ⑥ G5 内的收紧层：绝不放行前面任何一门 ───────────────────────────────────


def test_narrowing_never_opens_an_earlier_gate() -> None:
    """G2 未绑定的 external 工具：即便本次派发把 `task:run` 收成 ask，仍是 G2 HIDDEN。

    顺序不变、短路求值不变——收紧是 G5 内多的一个输入，不是第六道门，更不能反向放行。
    """
    ctx = _ctx(scope_overrides={SCOPE_TASK_RUN: 'ask'})
    decision = ToolExposurePolicy().evaluate(ctx, _ExternalTool())
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_SOURCE


def test_narrowing_cannot_relax_owner_deny() -> None:
    """主人在权限页 deny 的能力，派发侧配 ask 也放不开（doc18 §3.2 三态只能收紧）。"""
    tool = HasnCloudMcpServer().tool_registry.get_tool(RUN_NOW_TOOL)
    assert tool is not None
    ctx = _ctx(scope_overrides={SCOPE_TASK_RUN: 'ask'}, capability_modes={SCOPE_TASK_RUN: 'deny'})
    assert ctx.tool_mode(tool) == 'deny'


# ── ⑦ 主人权限页不被 per-dispatch 收紧污染 ──────────────────────────────────


def test_owner_scope_catalog_ignores_per_dispatch_narrowing() -> None:
    """权限页 catalog 只消费 G1/G2 硬边界：被本次派发收紧的工具仍须留在权限页供主人改回。

    否则复刻 102-B3「单向门」：主人在页面上看不到它，就再也调不回来了。
    """
    tool = HasnCloudMcpServer().tool_registry.get_tool(RUN_NOW_TOOL)
    assert tool is not None
    policy = ToolExposurePolicy()
    assert not policy.is_catalog_hidden(_ctx(scope_overrides=WORK_SESSION_OVERRIDES), tool)
