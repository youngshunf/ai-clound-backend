"""TOOLMIG2-P4：云端 MCP 面按 runtime_location 隐藏 deck/task/workflow（禁 mock）。

本地（device-hosted）分身的 runtime 同时挂本地面 + 云端面；deck/task/workflow 在本地面有
本地优先引擎，故对本地分身在云端面隐藏并拒绝，避免同一分身两个面看到重名工具。

验证（全部真实对象，无 mock）：
- 纯判定 runtime_visibility：仅 local + deck/task/workflow 命中，其余放行；
- 发现面 ToolDirectoryService._can_discover / search：本地分身看不到三域、云端分身照常；
- 执行面 server.call_tool：本地分身调 hasn.deck.create 被拒（连 tool.call 透传同源）。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpToolError
from backend.app.mcp.runtime_visibility import (
    LOCAL_HOSTED_HIDDEN_NAMESPACES,
    is_namespace_hidden_for_runtime,
    is_tool_hidden_for_runtime,
)
from backend.app.mcp.server import mcp_server
from backend.app.mcp.tool_directory import ToolSearchQuery

_HIDDEN_SAMPLE = ('hasn.deck.create', 'hasn.task.create', 'hasn.workflow.create')
_VISIBLE_SAMPLE = ('hasn.marketplace.list_skills', 'hasn.community.create_post', 'hasn.tool.search')


def _ctx(runtime_location: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_runtime_vis_test',
        owner_id=1,
        scopes=[],
        agent_status='active',
        metadata={},
        agent_name='可见性测试分身',
        owner_hasn_id='h_runtime_vis_test',
        session_uuid='amk_runtime_vis_test',
        runtime_location=runtime_location,
    )


# ── 纯判定（无依赖）──────────────────────────────────────────────────────────────
def test_namespaces_constant_is_the_three_domains() -> None:
    assert sorted(LOCAL_HOSTED_HIDDEN_NAMESPACES) == ['hasn.deck', 'hasn.task', 'hasn.workflow']


@pytest.mark.parametrize('tool_name', _HIDDEN_SAMPLE)
def test_local_runtime_hides_three_domains(tool_name: str) -> None:
    assert is_tool_hidden_for_runtime(tool_name, 'local') is True


@pytest.mark.parametrize('runtime_location', ['cloud', 'remote', '', None, 'LOCALish'])
def test_non_local_runtime_never_hides(runtime_location: str | None) -> None:
    for tool_name in _HIDDEN_SAMPLE:
        assert is_tool_hidden_for_runtime(tool_name, runtime_location) is False


@pytest.mark.parametrize('tool_name', _VISIBLE_SAMPLE)
def test_local_runtime_keeps_other_domains(tool_name: str) -> None:
    # 本地分身仍需云端面的其余域（community/marketplace/...）；只藏三域。
    assert is_tool_hidden_for_runtime(tool_name, 'local') is False


def test_namespace_helper_case_insensitive_on_location() -> None:
    assert is_namespace_hidden_for_runtime('hasn.deck', 'LOCAL') is True
    assert is_namespace_hidden_for_runtime('hasn.deck', ' local ') is True


# ── 发现面（真实 ToolDirectoryService + 真实注册表）────────────────────────────────
def test_can_discover_hides_for_local_shows_for_cloud() -> None:
    td = mcp_server.tool_directory
    deck_tool = mcp_server.tool_registry.get_tool('hasn.deck.create')
    assert deck_tool is not None, '云端 deck 工具应已注册（TOOLMIG2-P3）'

    assert td._can_discover(_ctx('local'), deck_tool) is False
    assert td._can_discover(_ctx('cloud'), deck_tool) is True


def test_can_discover_keeps_other_domains_for_local() -> None:
    td = mcp_server.tool_registry
    # 取一个非三域的已注册工具（marketplace），本地分身仍应可见。
    other = next(
        (t for t in td.get_all_tools() if t.name.startswith('hasn.marketplace.')),
        None,
    )
    assert other is not None, '应有 marketplace 工具用于对照'
    assert mcp_server.tool_directory._can_discover(_ctx('local'), other) is True


@pytest.mark.asyncio
async def test_search_excludes_three_domains_for_local() -> None:
    td = mcp_server.tool_directory
    for namespace in ('hasn.deck', 'hasn.task', 'hasn.workflow'):
        local_res = await td.search(_ctx('local'), ToolSearchQuery(query=namespace))
        assert local_res['tools'] == [], f'本地分身不应搜到 {namespace}'
        assert local_res['schemas'] == []

        cloud_res = await td.search(_ctx('cloud'), ToolSearchQuery(query=namespace))
        assert len(cloud_res['tools']) > 0, f'云端分身应能搜到 {namespace}'


# ── 执行面（真实 server.call_tool 守卫）────────────────────────────────────────────
@pytest.mark.asyncio
async def test_call_tool_rejects_three_domains_for_local() -> None:
    for tool_name in _HIDDEN_SAMPLE:
        with pytest.raises(McpToolError):
            await mcp_server.call_tool(_ctx('local'), tool_name, {})
