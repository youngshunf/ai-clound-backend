"""G1 平台特权门（doc18 §4.1 · 实施/103 U2）验收。

覆盖 doc18 §7 测试矩阵前三行 + catalog 行 + 守卫：
1. 普通分身（无授予）：search/call 双面 TOOL_NOT_FOUND（泛化 unknown-tool 不确认存在性）；
2. 授予后可见可调（Admin 授予表 ∪ ENV bootstrap · 精确 ∨ 段尾通配）；
3. owner 三态 deny 叠加收紧生效（G5 在 G1 之后，只收紧）；
4. owner 经 PUT /scopes（capability_modes allow）无法放行特权工具——三态非放行依据；
5. 权限页 catalog 行：普通分身 catalog 无 diag:*、运维分身有且 deny 后仍在（供改回）；
6. 守卫：注册表里命中特权前缀的 scope 必 ∈ PRIVILEGED_SCOPES（防漏名单）；
   PLATFORM_SCOPE_CATALOG 键不得命中特权前缀（前缀排他，防误把普通能力划特权）。

判定本体零 mock；工具加载/审计 no-op（与 test_tool_exposure 同款接缝）。
真实 PG grants 活取 + ENV 合并另见 test_g1_privilege_pg.py。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.platform_scopes import (
    PLATFORM_SCOPE_CATALOG,
    PRIVILEGED_SCOPES,
    grant_matches_scope,
    is_privileged_scope,
    is_valid_privileged_grant,
    privileged_scopes_satisfied,
)
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tool_exposure import (
    ACTION_ALLOW,
    ACTION_HIDDEN,
    GATE_PRIVILEGE,
    REASON_PRIVILEGED,
    ToolExposurePolicy,
)
from backend.app.mcp.tools.base import BaseTool

DIAG_TOOL = 'hasn.diag.list_issues'
DIAG_SCOPE = 'diag:read:all'


class _DiagTool(BaseTool):
    """运维特权 stub 工具（required_scopes 命中特权名单），execute 仅回显。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return DIAG_TOOL

    @property
    def description(self) -> str:
        return 'diag list issues (privileged)'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def required_scopes(self) -> list[str]:
        return [DIAG_SCOPE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'executed': True}


def _ctx(*, granted: set[str] | None = None, capability_modes: dict | None = None) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_g1_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_g1_test',
        session_uuid='amk_g1_test',
        capability_modes=capability_modes or {},
    )
    ctx.granted_privileged_scopes = frozenset(granted or set())
    return ctx


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# ── 1. evaluate 纯函数：G1 逐态 ────────────────────────────────────────────


def test_g1_ungranted_hidden() -> None:
    decision = ToolExposurePolicy().evaluate(_ctx(), _DiagTool())
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_PRIVILEGE
    assert decision.reason == REASON_PRIVILEGED


def test_g1_granted_exact_allowed() -> None:
    decision = ToolExposurePolicy().evaluate(_ctx(granted={DIAG_SCOPE}), _DiagTool())
    assert decision.action == ACTION_ALLOW


def test_g1_granted_wildcard_allowed() -> None:
    # 段尾整段通配 diag:* 覆盖 diag:read:all
    decision = ToolExposurePolicy().evaluate(_ctx(granted={'diag:*'}), _DiagTool())
    assert decision.action == ACTION_ALLOW


def test_g1_wrong_grant_still_hidden() -> None:
    # 持有别的特权（diag:manage）不覆盖 diag:read:all
    decision = ToolExposurePolicy().evaluate(_ctx(granted={'diag:manage'}), _DiagTool())
    assert decision.action == ACTION_HIDDEN
    assert decision.reason == REASON_PRIVILEGED


def test_g1_before_g5_owner_deny_tightens() -> None:
    # 已授予特权，但 owner 三态 deny → G5 在 G1 之后只收紧 → HIDDEN(owner_denied)
    decision = ToolExposurePolicy().evaluate(
        _ctx(granted={DIAG_SCOPE}, capability_modes={DIAG_SCOPE: 'deny'}), _DiagTool()
    )
    assert decision.action == ACTION_HIDDEN
    assert decision.gate != GATE_PRIVILEGE  # 是 G5 owner 门，不是 G1


def test_g1_capability_mode_allow_is_not_a_grant() -> None:
    """三态非放行依据：owner 把 diag:read:all 设 allow 也放不行特权工具（未经 Admin 授予表）。"""
    decision = ToolExposurePolicy().evaluate(
        _ctx(granted=set(), capability_modes={DIAG_SCOPE: 'allow'}), _DiagTool()
    )
    assert decision.action == ACTION_HIDDEN
    assert decision.gate == GATE_PRIVILEGE


# ── 2. 双面一致性（发现面隐身 + 执行面 TOOL_NOT_FOUND 泛化） ──────────────


def test_g1_faces_consistent_ungranted(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())
    ctx = _ctx()

    tool = server.tool_registry.get_tool(DIAG_TOOL)
    # 发现面不可见
    assert not server.tool_directory._can_discover(ctx, tool)


@pytest.mark.asyncio
async def test_g1_call_face_generic_tool_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """特权隐身的执行面错误与「真·未注册工具」逐字节同款，且不泄露特权/运维语义。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())
    ctx = _ctx()

    # 授予后隐身的特权工具（存在但对普通分身不可见）
    with pytest.raises(McpToolError) as exc_privileged:
        await server.call_tool(ctx, DIAG_TOOL, {})
    # 根本不存在的工具走同一泛化 unknown-tool 路径（_resolve_tool 真 404）
    with pytest.raises(McpToolError) as exc_absent:
        await server.call_tool(ctx, 'hasn.totally.made_up_xyz', {})

    # 不可区分性：同错误码 + 同文案模板（仅回显调用方自传的名字不同）——攻击者无法据措辞侧探存在性
    assert exc_privileged.value.code == exc_absent.value.code == McpErrorCode.TOOL_NOT_FOUND
    assert exc_privileged.value.message == f'Tool not found: {DIAG_TOOL}'
    assert exc_absent.value.message == 'Tool not found: hasn.totally.made_up_xyz'
    # 把两者的工具名抹平后必须逐字节一致（同一文案模板）
    assert exc_privileged.value.message.replace(DIAG_TOOL, 'X') == exc_absent.value.message.replace(
        'hasn.totally.made_up_xyz', 'X'
    )
    # 绝不泄露「为什么」：文案里不得出现特权/运维/被拒等语义词
    for word in ('运维', 'privileged', 'operator', '特权', 'denied', 'forbidden'):
        assert word not in exc_privileged.value.message.lower()


@pytest.mark.asyncio
async def test_g1_granted_visible_and_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())
    ctx = _ctx(granted={DIAG_SCOPE})

    tool = server.tool_registry.get_tool(DIAG_TOOL)
    assert server.tool_directory._can_discover(ctx, tool)
    result = await server.call_tool(ctx, DIAG_TOOL, {})
    assert result == {'executed': True}


@pytest.mark.asyncio
async def test_g1_owner_deny_over_grant_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """已授予 + owner deny：发现面隐身、执行面 PermissionError（G5 现状，U5 拍板）。"""
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())
    ctx = _ctx(granted={DIAG_SCOPE}, capability_modes={DIAG_SCOPE: 'deny'})

    tool = server.tool_registry.get_tool(DIAG_TOOL)
    assert not server.tool_directory._can_discover(ctx, tool)
    with pytest.raises(PermissionError):
        await server.call_tool(ctx, DIAG_TOOL, {})


# ── 3. 权限页 catalog 第四暴露面（doc18 §3.2） ────────────────────────────


def test_catalog_hides_privileged_for_ordinary_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())
    ctx = _ctx()  # 普通分身，无特权授予

    catalog = server.tool_directory.build_scope_catalog(ctx)
    all_keys = {cap['key'] for src in catalog['sources'] for cap in src['capabilities']}
    all_tools = {t for src in catalog['sources'] for cap in src['capabilities'] for t in cap['tools']}
    assert DIAG_SCOPE not in all_keys  # 权限页无 diag:* 能力
    assert DIAG_TOOL not in all_tools  # 也无工具名（存在性不侧漏）


def test_catalog_shows_privileged_for_operator_and_keeps_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    server.tool_registry.register(_DiagTool())

    # 运维分身（持授予）：catalog 含 diag:*
    op_ctx = _ctx(granted={DIAG_SCOPE})
    catalog = server.tool_directory.build_scope_catalog(op_ctx)
    keys = {cap['key'] for src in catalog['sources'] for cap in src['capabilities']}
    assert DIAG_SCOPE in keys

    # owner 对运维分身设 deny：G5 三态永不参与 catalog 隐藏 → 仍在 catalog 供改回（不复刻 102-B3 单向门）
    deny_ctx = _ctx(granted={DIAG_SCOPE}, capability_modes={DIAG_SCOPE: 'deny'})
    catalog2 = server.tool_directory.build_scope_catalog(deny_ctx)
    keys2 = {cap['key'] for src in catalog2['sources'] for cap in src['capabilities']}
    assert DIAG_SCOPE in keys2


# ── 4. 守卫：防漂移 + 前缀排他 ────────────────────────────────────────────


def test_guard_registry_privileged_scopes_are_listed() -> None:
    """注册表里凡 required_scopes 命中特权前缀的工具，其 scope 必 ∈ PRIVILEGED_SCOPES（防漏名单）。"""
    server = HasnCloudMcpServer()
    for tool in server.tool_registry.get_all_tools():
        for scope in getattr(tool, 'required_scopes', []) or []:
            if is_privileged_scope(scope):
                assert scope in PRIVILEGED_SCOPES, f'{tool.name} 声明特权 scope {scope} 未登记进 PRIVILEGED_SCOPES'


def test_guard_platform_catalog_keys_not_privileged() -> None:
    """前缀排他（防漂移）：PLATFORM_SCOPE_CATALOG 是展示元数据注册表（scope_meta 查表源），
    可含**有意登记**的特权 scope 元数据（如 diag:read:all/diag:manage，供运维分身工具可见时
    查 label/risk/描述）——凡命中特权前缀的键必须 ∈ PRIVILEGED_SCOPES（表明确为运维特权）。
    反之，owner 级普通能力键若误用特权前缀却未登记 PRIVILEGED_SCOPES = 漂移（会被 G1 强划
    特权而错误隐身），必须失败；owner 自查类须走 selfdiag: 等非特权前缀。
    注：真正的「第四暴露面」隐藏在 build_scope_catalog 的 is_catalog_hidden（工具级 G1 过滤），
    与本词表内容无关——本词表仅元数据查表，不是 owner 权限页的枚举源。"""
    for scope_key in PLATFORM_SCOPE_CATALOG:
        if is_privileged_scope(scope_key):
            assert scope_key in PRIVILEGED_SCOPES, (
                f'PLATFORM_SCOPE_CATALOG 键 {scope_key} 命中特权前缀但未登记进 PRIVILEGED_SCOPES'
                '（owner 级普通能力误用特权前缀 = 漂移；owner 自查类请用 selfdiag: 等非特权前缀）'
            )


def test_grant_matching_semantics() -> None:
    assert grant_matches_scope('diag:read:all', 'diag:read:all')
    assert grant_matches_scope('diag:*', 'diag:read:all')
    assert grant_matches_scope('diag:*', 'diag:manage')
    assert not grant_matches_scope('diag:*', 'ops:x')
    assert not grant_matches_scope('diag:read:all', 'diag:manage')
    assert privileged_scopes_satisfied({'diag:read:all'}, {'diag:*'})
    assert not privileged_scopes_satisfied({'diag:manage', 'diag:read:all'}, {'diag:read:all'})


def test_valid_privileged_grant_format() -> None:
    assert is_valid_privileged_grant('diag:read:all')
    assert is_valid_privileged_grant('ops:*')
    assert not is_valid_privileged_grant('media:generate')  # 非特权前缀
    assert not is_valid_privileged_grant('ops:*:x')  # * 不在末段
    assert not is_valid_privileged_grant('diag:')  # 空尾段
