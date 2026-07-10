"""TOOLMIG2-P4：云端 MCP 面按 runtime_location 隐藏 task/workflow（禁 mock）。

本地（device-hosted）分身的 runtime 同时挂本地面 + 云端面；task/workflow 在本地面有
本地优先引擎（且云端有同名孪生），故对本地分身在云端面隐藏并拒绝，避免同一分身两个面
看到重名工具、写到不同存储。

⚠️ deck **不隐藏**（2026-07-10 修）：deck 已 TOOLMIG2-P3 完整迁云端、本地 hasn-mcp 无
孪生，云端是唯一来源；本地分身必须能在云端面发现并调用 deck，否则两面都够不到（违反
「不管分身在哪里都要能调用云端工具」）。本文件锁住这一回归。

验证（全部真实对象，无 mock）：
- 纯判定 runtime_visibility：仅 local + task/workflow 命中，deck 与其余域一律放行；
- 发现面 ToolDirectoryService._can_discover / search：本地分身看不到 task/workflow、
  但**看得到 deck**；云端分身照常；
- 执行面 server.call_tool：本地分身调 hasn.task/workflow.create 被拒；deck 不被本收口拦。
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

# 仍隐藏的两域（有本地孪生）
_HIDDEN_SAMPLE = ('hasn.task.create', 'hasn.workflow.create')
# deck 已完整迁云端、无本地孪生——本地分身在云端面必须可见可调
_DECK_SAMPLE = ('hasn.deck.create', 'hasn.deck.list')
_VISIBLE_SAMPLE = ('hasn.marketplace.list_skills', 'hasn.community.create_post', 'hasn.tool.search')


def _ctx(runtime_location: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_runtime_vis_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='可见性测试分身',
        owner_hasn_id='h_runtime_vis_test',
        session_uuid='amk_runtime_vis_test',
        runtime_location=runtime_location,
    )


# ── 纯判定（无依赖）──────────────────────────────────────────────────────────────
def test_namespaces_constant_is_the_two_local_hosted_domains() -> None:
    # deck 已移出（迁云端无本地孪生）；只剩 task/workflow 两域有本地引擎需去重。
    assert sorted(LOCAL_HOSTED_HIDDEN_NAMESPACES) == ['hasn.task', 'hasn.workflow']


@pytest.mark.parametrize('tool_name', _HIDDEN_SAMPLE)
def test_local_runtime_hides_task_and_workflow(tool_name: str) -> None:
    assert is_tool_hidden_for_runtime(tool_name, 'local') is True


@pytest.mark.parametrize('tool_name', _DECK_SAMPLE)
def test_local_runtime_never_hides_deck(tool_name: str) -> None:
    # 回归锁：deck 无本地孪生，本地分身在云端面必须可达（不管分身在哪都能调云端工具）。
    assert is_tool_hidden_for_runtime(tool_name, 'local') is False


@pytest.mark.parametrize('runtime_location', ['cloud', 'remote', '', None, 'LOCALish'])
def test_non_local_runtime_never_hides(runtime_location: str | None) -> None:
    for tool_name in _HIDDEN_SAMPLE:
        assert is_tool_hidden_for_runtime(tool_name, runtime_location) is False


@pytest.mark.parametrize('tool_name', _VISIBLE_SAMPLE)
def test_local_runtime_keeps_other_domains(tool_name: str) -> None:
    # 本地分身仍需云端面的其余域（community/marketplace/...）；只藏 task/workflow。
    assert is_tool_hidden_for_runtime(tool_name, 'local') is False


def test_namespace_helper_case_insensitive_on_location() -> None:
    assert is_namespace_hidden_for_runtime('hasn.task', 'LOCAL') is True
    assert is_namespace_hidden_for_runtime('hasn.task', ' local ') is True


# ── 发现面（真实 ToolDirectoryService + 真实注册表）────────────────────────────────
def test_can_discover_shows_deck_for_local_and_cloud() -> None:
    td = mcp_server.tool_directory
    deck_tool = mcp_server.tool_registry.get_tool('hasn.deck.create')
    assert deck_tool is not None, '云端 deck 工具应已注册（TOOLMIG2-P3）'

    # deck 无本地孪生：本地与云端分身都应能发现。
    assert td._can_discover(_ctx('local'), deck_tool) is True
    assert td._can_discover(_ctx('cloud'), deck_tool) is True


def test_can_discover_hides_task_for_local_shows_for_cloud() -> None:
    td = mcp_server.tool_directory
    task_tool = mcp_server.tool_registry.get_tool('hasn.task.create')
    assert task_tool is not None, '云端 task 工具应已注册'

    assert td._can_discover(_ctx('local'), task_tool) is False
    assert td._can_discover(_ctx('cloud'), task_tool) is True


def test_can_discover_keeps_other_domains_for_local() -> None:
    td = mcp_server.tool_registry
    # 取一个非隐藏域的已注册工具（marketplace），本地分身仍应可见。
    other = next(
        (t for t in td.get_all_tools() if t.name.startswith('hasn.marketplace.')),
        None,
    )
    assert other is not None, '应有 marketplace 工具用于对照'
    assert mcp_server.tool_directory._can_discover(_ctx('local'), other) is True


@pytest.mark.asyncio
async def test_search_excludes_task_workflow_but_keeps_deck_for_local() -> None:
    td = mcp_server.tool_directory
    # task/workflow：本地分身搜不到（用本地面那份），云端分身照常。
    for namespace in ('hasn.task', 'hasn.workflow'):
        local_res = await td.search(_ctx('local'), ToolSearchQuery(query=namespace))
        assert local_res['tools'] == [], f'本地分身不应搜到 {namespace}'
        assert local_res['schemas'] == []

        cloud_res = await td.search(_ctx('cloud'), ToolSearchQuery(query=namespace))
        assert len(cloud_res['tools']) > 0, f'云端分身应能搜到 {namespace}'

    # deck：本地分身也应搜得到（云端唯一来源）。
    deck_local = await td.search(_ctx('local'), ToolSearchQuery(query='hasn.deck'))
    assert len(deck_local['tools']) > 0, '本地分身应能搜到 deck（云端唯一来源）'


# ── 执行面（真实 server.call_tool 守卫）────────────────────────────────────────────
@pytest.mark.asyncio
async def test_call_tool_rejects_task_workflow_for_local() -> None:
    for tool_name in _HIDDEN_SAMPLE:
        with pytest.raises(McpToolError):
            await mcp_server.call_tool(_ctx('local'), tool_name, {})


@pytest.mark.asyncio
async def test_call_tool_deck_not_blocked_by_runtime_guard_for_local() -> None:
    # deck 不该被运行位置收口拦：本地分身调 deck.list（读类、参数最省）不得抛出
    # 「本地分身请使用本地工具面」这一类 TOOL_NOT_FOUND。允许其它业务错误（鉴权/空数据等），
    # 只断言不是运行位置收口造成的拒绝。
    try:
        await mcp_server.call_tool(_ctx('local'), 'hasn.deck.list', {})
    except McpToolError as exc:
        assert '本地工具面' not in str(exc), f'deck 不应被运行位置收口拦下：{exc}'
