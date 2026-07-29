"""工作会话精确工具白名单的云端硬门禁。"""

from __future__ import annotations

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import mcp_server
from backend.app.mcp.tool_directory import ToolDirectoryService
from backend.app.mcp.tools.registry import BOOTSTRAP_TOOL_NAMES
from backend.app.mcp.trust_gate import (
    RESERVED_ALLOWED_TOOL_NAMES,
    apply_session_tool_allowlist_header,
    is_session_tool_allowed,
    pop_allowed_tool_names,
)


def _context() -> AgentContext:
    return AgentContext(
        hasn_id='a_session_allowlist',
        owner_id=1001,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_owner',
    )


def test_reserved_allowlist_preserves_exact_names_and_empty_list() -> None:
    arguments, allowed = pop_allowed_tool_names({
        'query': 'sources',
        RESERVED_ALLOWED_TOOL_NAMES: ['hasn.session.ask', 'hasn.project.create'],
    })
    assert arguments == {'query': 'sources'}
    assert allowed == frozenset({'hasn.session.ask', 'hasn.project.create'})

    arguments, allowed = pop_allowed_tool_names({RESERVED_ALLOWED_TOOL_NAMES: []})
    assert arguments == {}
    assert allowed == frozenset()


@pytest.mark.parametrize(
    'invalid',
    [
        'hasn.session.ask',
        ['hasn.session.ask', 'hasn.session.ask'],
        [''],
        ['not-canonical'],
    ],
)
def test_reserved_allowlist_rejects_invalid_policy(invalid: object) -> None:
    with pytest.raises(McpToolError) as exc_info:
        pop_allowed_tool_names({RESERVED_ALLOWED_TOOL_NAMES: invalid})
    assert exc_info.value.code == McpErrorCode.TOOL_NOT_ALLOWED


def test_real_directory_only_exposes_bootstrap_and_allowed_business_tool() -> None:
    context = _context()
    all_tools = mcp_server.tool_registry.get_all_tools()
    allowed_name = next(tool.name for tool in all_tools if tool.name not in BOOTSTRAP_TOOL_NAMES)
    context.allowed_tool_names = frozenset({allowed_name})

    visible = ToolDirectoryService(mcp_server.tool_registry).list_all_tools(context)
    visible_names = {tool['name'] for tool in visible}

    assert allowed_name in visible_names
    assert visible_names <= BOOTSTRAP_TOOL_NAMES | {allowed_name}


def test_session_policy_keeps_transport_wrappers_but_rejects_other_tools() -> None:
    context = _context()
    context.allowed_tool_names = frozenset({'hasn.session.ask', 'hasn.project.create'})

    assert is_session_tool_allowed(context, 'hasn.cloud.tool.search')
    assert is_session_tool_allowed(context, 'hasn.cloud.tool.call')
    assert is_session_tool_allowed(context, 'hasn.session.ask')
    assert not is_session_tool_allowed(context, 'hasn.imagelab.apply_recipe')


def test_cli_header_sets_exact_allowlist_on_real_agent_context() -> None:
    context = _context()
    apply_session_tool_allowlist_header(
        context,
        {b'x-hasn-allowed-tools': b'["hasn.session.ask","hasn.project.create"]'},
    )
    assert context.allowed_tool_names == frozenset({'hasn.session.ask', 'hasn.project.create'})


@pytest.mark.asyncio
async def test_server_rejects_unlisted_tool_before_execution() -> None:
    context = _context()
    context.allowed_tool_names = frozenset()

    with pytest.raises(McpToolError) as exc_info:
        await mcp_server.call_tool(context, 'hasn.task.list', {})

    assert exc_info.value.code == McpErrorCode.TOOL_NOT_ALLOWED


@pytest.mark.asyncio
async def test_tool_call_wrapper_rejects_unlisted_inner_tool() -> None:
    context = _context()
    context.allowed_tool_names = frozenset({'hasn.session.ask', 'hasn.project.create'})

    with pytest.raises(McpToolError) as exc_info:
        await mcp_server.call_tool(
            context,
            'hasn.cloud.tool.call',
            {'name': 'hasn.task.list', 'params': {}},
        )

    assert exc_info.value.code == McpErrorCode.TOOL_NOT_ALLOWED
