"""P7 — external MCP 工具发现/调度门控（替代 P9「只预留」回归）。

P7 起 external 来源已接通：工具实例全局共享，发现/调度资格按本 Agent
`external_allowed_tools` 集合（gate1 owner 启用 + gate2 binding，由 server.py 注入）过滤。

断言：
1. ToolSource 字面量含 'external'（契约）。
2. catalog external 分组：未绑定 → 空；注入授权集合 → 该工具进 external 分组。
3. _can_discover：external 工具只有在授权集合内才可发现（杜绝串号）。
4. _dispatch_by_source：授权集合内 → 放行 execute；不在 → DIRECT_CALL_DENIED（不静默成功）。

纯单元：不建表、不连真实外部 MCP（execute 桩不触达网关）。
"""

from __future__ import annotations

import typing

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import mcp_server
from backend.app.mcp.tool_directory import ToolSource


def _ctx(*, allowed: set[str] | None = None) -> AgentContext:
    ctx = AgentContext(
        hasn_id='a_external_reserved',
        owner_id=0,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id='h_external_reserved',
        session_uuid='catalog:a_external_reserved',
    )
    ctx.external_allowed_tools = allowed or set()
    return ctx


class _ExternalTool:
    name = 'hasn.ext.acme.do'
    source = 'external'
    risk_level = 'medium'
    required_scopes = ['mcp:tool://hasn.ext.acme.do']

    @property
    def description(self) -> str:
        return 'acme do'

    async def execute(self, agent_context: AgentContext, arguments: dict) -> dict:
        return {'executed': True}


def test_tool_source_literal_reserves_external() -> None:
    assert 'external' in typing.get_args(ToolSource)


def test_catalog_external_group_empty_when_unbound() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx())
    by_source = {s['source']: s for s in catalog['sources']}
    assert 'external' in by_source, 'external 分组结构必须存在'
    assert by_source['external']['capabilities'] == [], '未绑定 Agent → external 分组为空'


def test_can_discover_gated_by_allowed_set() -> None:
    """external 工具只有在 external_allowed_tools 集合内才可发现。"""
    directory = mcp_server.tool_directory
    tool = _ExternalTool()
    assert directory._can_discover(_ctx(allowed=set()), tool) is False
    assert directory._can_discover(_ctx(allowed={'hasn.ext.acme.do'}), tool) is True


@pytest.mark.asyncio
async def test_dispatch_external_denied_when_not_allowed() -> None:
    """不在授权集合 → DIRECT_CALL_DENIED，不静默成功。"""
    tool = _ExternalTool()
    with pytest.raises(McpToolError) as exc:
        await mcp_server._dispatch_by_source(_ctx(allowed=set()), tool, 'external', {})
    assert exc.value.code == McpErrorCode.DIRECT_CALL_DENIED


@pytest.mark.asyncio
async def test_dispatch_external_allowed_executes() -> None:
    """在授权集合内 → 放行到 execute（桩返回 executed=True）。"""
    tool = _ExternalTool()
    result = await mcp_server._dispatch_by_source(_ctx(allowed={'hasn.ext.acme.do'}), tool, 'external', {})
    assert result == {'executed': True}
