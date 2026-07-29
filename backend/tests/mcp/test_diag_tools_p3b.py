"""DIAG-P3b：`hasn.diag.*` 六工具注册 + doc18 §7 平台特权门矩阵（对真实注册工具，非 stub）。

`test_g1_privilege.py` 已用 stub 验证 G1 判定本体；本文件验证**真实注册的 6 个 diag 工具**
（server 启动即注册）确实：
1. 注册齐全、命名/scope/execution_location 正确（读=diag:read:all，写=diag:manage，均 cloud）；
2. 两 scope 命中特权前缀 → G1 平台特权门对**每个** diag 工具生效（普通分身双面 TOOL_NOT_FOUND，
   运维分身可见可调）；
3. 出厂三态 Allow（default_mode=allow + 无 manifest human_confirmation → 运维分身拿到特权即放行）；
4. 权限页 catalog：普通分身无 diag:*、运维分身有且 owner deny 后仍在（供改回，不复刻单向门）。

判定/暴露本体零 mock；工具 IO（app/external 加载、审计）no-op（与 test_g1_privilege 同接缝）。
真实 execute（wrapper→service→PG）另见 tests/hasn_diag/test_diag_tools_execute_pg.py。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.diag import DIAG_TOOLS, NAMESPACE, SCOPE_MANAGE, SCOPE_READ

_READ_TOOLS = {'hasn.diag.list_issues', 'hasn.diag.get_issue', 'hasn.diag.list_occurrences', 'hasn.diag.stats'}
_WRITE_TOOLS = {'hasn.diag.update_issue', 'hasn.diag.resolve_issue'}
_ALL_TOOLS = _READ_TOOLS | _WRITE_TOOLS


def _ctx(*, granted: set[str] | None = None, capability_modes: dict | None = None) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_diag_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_diag_test',
        session_uuid='amk_diag_test',
        capability_modes=capability_modes or {},
    )
    ctx.granted_privileged_scopes = frozenset(granted or set())
    return ctx


def _operator_ctx(**kw: Any) -> AgentContext:
    """平台运维分析师：持 diag:*（Admin 授予表 ∪ ENV bootstrap 现查灌入）。"""
    return _ctx(granted={'diag:*'}, **kw)


def _server_with_noop_io(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


# ── 1. 注册 + 声明正确性 ──────────────────────────────────────────────────────


def test_diag_tools_registered_with_correct_shape() -> None:
    names = {t.name for t in DIAG_TOOLS}
    assert names == _ALL_TOOLS, f'diag 工具集应恰为 6 个：{names}'
    for tool in DIAG_TOOLS:
        assert tool.namespace == NAMESPACE
        assert tool.source == 'platform'
        assert tool.execution_location == 'cloud'
        expected = [SCOPE_READ] if tool.name in _READ_TOOLS else [SCOPE_MANAGE]
        assert tool.required_scopes == expected, f'{tool.name} scope 应为 {expected}'


def test_diag_tools_registered_in_server() -> None:
    """server 启动即把 6 个 diag 工具注册进 registry（不是只在模块常量里）。"""
    server = HasnCloudMcpServer()
    registered = {t.name for t in server.tool_registry.get_all_tools() if t.name.startswith('hasn.diag.')}
    assert registered == _ALL_TOOLS


# ── 2. G1 平台特权门矩阵（对每个真实 diag 工具） ──────────────────────────────


@pytest.mark.asyncio
async def test_ordinary_agent_call_face_tool_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通分身（无授予）对读/写 diag 工具执行面均 TOOL_NOT_FOUND，与真·未注册工具逐字节同款。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx()

    for tool_name in sorted(_ALL_TOOLS):
        with pytest.raises(McpToolError) as exc_privileged:
            await server.call_tool(ctx, tool_name, {'fingerprint': 'x'})
        with pytest.raises(McpToolError) as exc_absent:
            await server.call_tool(ctx, 'hasn.totally.made_up_xyz', {})
        assert exc_privileged.value.code == exc_absent.value.code == McpErrorCode.TOOL_NOT_FOUND
        # 抹平工具名后逐字节一致（同一泛化文案模板，不侧漏存在性）
        assert exc_privileged.value.message.replace(tool_name, 'X') == exc_absent.value.message.replace(
            'hasn.totally.made_up_xyz', 'X'
        )
        # 绝不泄露「为什么」
        for word in ('运维', 'privileged', 'operator', '特权', 'denied'):
            assert word not in exc_privileged.value.message.lower()


def test_ordinary_agent_cannot_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx()
    for tool_name in _ALL_TOOLS:
        tool = server.tool_registry.get_tool(tool_name)
        assert tool is not None
        assert not server.tool_directory._can_discover(ctx, tool), f'普通分身不应发现 {tool_name}'


def test_operator_can_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    ctx = _operator_ctx()
    for tool_name in _ALL_TOOLS:
        tool = server.tool_registry.get_tool(tool_name)
        assert tool is not None
        assert server.tool_directory._can_discover(ctx, tool), f'运维分身应发现 {tool_name}'


def test_capability_mode_allow_is_not_a_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """三态非放行依据：owner 把 diag:read:all 设 allow 也放不行（未经 Admin 授予表）。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx(granted=set(), capability_modes={SCOPE_READ: 'allow'})
    tool = server.tool_registry.get_tool('hasn.diag.list_issues')
    assert tool is not None
    assert not server.tool_directory._can_discover(ctx, tool)


@pytest.mark.asyncio
async def test_operator_owner_deny_tightens(monkeypatch: pytest.MonkeyPatch) -> None:
    """已授予 + owner deny：G5 在 G1 之后只收紧 → 发现面隐身、执行面 PermissionError。"""
    server = _server_with_noop_io(monkeypatch)
    ctx = _operator_ctx(capability_modes={SCOPE_READ: 'deny'})
    tool = server.tool_registry.get_tool('hasn.diag.list_issues')
    assert tool is not None
    assert not server.tool_directory._can_discover(ctx, tool)
    with pytest.raises(PermissionError):
        await server.call_tool(ctx, 'hasn.diag.list_issues', {})


# ── 3. 出厂三态 Allow ─────────────────────────────────────────────────────────


def test_factory_default_mode_is_allow() -> None:
    """默认三态 Allow：运维分身拿到特权即放行（default_mode=allow + 无 manifest human_confirmation）。"""
    ctx = _operator_ctx()  # default_mode 缺省 'allow'
    for tool in DIAG_TOOLS:
        assert ctx.tool_mode(tool) == 'allow', f'{tool.name} 出厂三态应 Allow'


# ── 4. 权限页 catalog 第四暴露面 ──────────────────────────────────────────────


def test_catalog_hides_diag_for_ordinary(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)
    ctx = _ctx()
    catalog = server.tool_directory.build_scope_catalog(ctx)
    keys = {cap['key'] for src in catalog['sources'] for cap in src['capabilities']}
    tools = {t for src in catalog['sources'] for cap in src['capabilities'] for t in cap['tools']}
    assert SCOPE_READ not in keys and SCOPE_MANAGE not in keys
    assert not (_ALL_TOOLS & tools), '权限页不得侧漏 diag 工具名'


def test_catalog_shows_diag_for_operator_and_keeps_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server_with_noop_io(monkeypatch)

    op = server.tool_directory.build_scope_catalog(_operator_ctx())
    op_keys = {cap['key'] for src in op['sources'] for cap in src['capabilities']}
    assert SCOPE_READ in op_keys and SCOPE_MANAGE in op_keys

    # owner deny：G5 三态永不参与 catalog 隐藏 → 仍在（供改回，不复刻单向门）
    deny = server.tool_directory.build_scope_catalog(_operator_ctx(capability_modes={SCOPE_READ: 'deny'}))
    deny_keys = {cap['key'] for src in deny['sources'] for cap in src['capabilities']}
    assert SCOPE_READ in deny_keys
