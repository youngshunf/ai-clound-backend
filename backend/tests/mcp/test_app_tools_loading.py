"""P4-B — App 工具加载（manifest capability → MCP App 工具）。

验证 GAP 闭合：已发布 AI-Native manifest 的 capability 被投影成 app-source 工具，
进入发现；三态 mode 门控对 App 工具同样生效。零 fake：无 manifest → 空。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.service.ai_native_builtin_manifests import (
    COMMUNITY_AI_NATIVE_MANIFEST,
    KNOWLEDGE_AI_NATIVE_MANIFEST,
)
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.app_tool_loader import (
    build_app_tools_from_manifest,
    load_published_app_tools,
)


def _payload(manifest: dict) -> dict:
    return {'id': None, 'app_id': manifest['app_id'], 'manifest_json': manifest}


def _ctx(default_mode: str = 'allow', capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id='a_app',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_app',
        session_uuid='amk_app',
        default_mode=default_mode,
        capability_modes=capability_modes,
    )


def test_build_app_tools_from_community_manifest() -> None:
    tools = build_app_tools_from_manifest(_payload(COMMUNITY_AI_NATIVE_MANIFEST))
    names = {t.name for t in tools}
    assert 'hasn.community.get_feed' in names
    assert 'hasn.community.create_post' in names
    by_name = {t.name: t for t in tools}
    assert by_name['hasn.community.get_feed'].source == 'app'
    assert by_name['hasn.community.get_feed'].required_scopes == ['community:read']
    assert by_name['hasn.community.create_post'].required_scopes == ['community:post']
    assert by_name['hasn.community.create_post'].risk_level == 'medium'


def test_build_app_tools_zero_fake_on_empty() -> None:
    assert build_app_tools_from_manifest({}) == []
    assert build_app_tools_from_manifest({'manifest_json': {'app_id': 'x', 'capabilities': []}}) == []


def test_build_app_tools_skips_local_data_plane_manifest() -> None:
    """本地数据面应用（tools[] 置空、capability 仅承载发现元数据）不投影成云端工具。

    回归：曾把这类 capability 全标 execution_location=cloud 投影，导致 tool.search 宣称
    可云端调用、实际无云端 handler → 调用报「AI-Native 工具不存在」（发现/调度契约漂移）。
    """
    manifest = {
        'app_id': 'localapp',
        'tools': [],  # 数据面在本地 hasn-mcp，云端无 handler
        'capabilities': [
            {'mcp_name': 'hasn.localapp.list', 'tool_id': 'localapp.list', 'description': 'list'},
            {'mcp_name': 'hasn.localapp.get', 'tool_id': 'localapp.get', 'description': 'get'},
        ],
    }
    assert build_app_tools_from_manifest(_payload(manifest)) == []


def test_build_app_tools_only_projects_cloud_dispatchable_capabilities() -> None:
    """混合 manifest：仅投影 tool_id 命中 tools[]（云端 handler 清单）的 capability。

    与 ai_native_runtime_gateway._find_tool 的调度权威一致——发现面不得宽于调度面。
    """
    manifest = {
        'app_id': 'mixed',
        'tools': [{'tool_id': 'mixed.cloud_op'}],  # 仅此有云端 handler
        'capabilities': [
            {'mcp_name': 'hasn.mixed.cloud_op', 'tool_id': 'mixed.cloud_op', 'description': 'cloud'},
            {'mcp_name': 'hasn.mixed.local_op', 'tool_id': 'mixed.local_op', 'description': 'local'},
        ],
    }
    names = {t.name for t in build_app_tools_from_manifest(_payload(manifest))}
    assert names == {'hasn.mixed.cloud_op'}, '只有有云端 handler 的能力进发现面'


@pytest.mark.asyncio
async def test_load_published_app_tools_projects_builtins() -> None:
    payloads = [_payload(COMMUNITY_AI_NATIVE_MANIFEST), _payload(KNOWLEDGE_AI_NATIVE_MANIFEST)]
    with patch(
        'backend.app.hasn.service.ai_native_app_registry.ai_native_app_registry.list_published_manifests',
        new=AsyncMock(return_value=payloads),
    ):
        tools = await load_published_app_tools()
    names = {t.name for t in tools}
    assert 'hasn.community.get_feed' in names
    assert 'hasn.knowledge.search' in names
    assert all(t.source == 'app' for t in tools)


@pytest.mark.asyncio
async def test_app_tools_appear_in_search_and_mode_deny_hides() -> None:
    from backend.app.mcp.tool_directory import ToolSearchQuery

    # 注册 community App 工具进一个干净的 server
    server = HasnCloudMcpServer()
    for tool in build_app_tools_from_manifest(_payload(COMMUNITY_AI_NATIVE_MANIFEST)):
        if server.tool_registry.get_tool(tool.name) is None:
            server.tool_registry.register(tool)
    directory = server.tool_directory
    query = ToolSearchQuery(query='hasn.community', source='all', detail='summary', page_size=50)

    # 默认全开：App 工具进入发现
    ctx_allow = _ctx()
    allow_names = {t['name'] for t in (await directory.search(ctx_allow, query))['tools']}
    assert 'hasn.community.get_feed' in allow_names
    assert 'hasn.community.create_post' in allow_names

    # owner 把 create_post deny → 该工具从发现消失（mode 门控对 App 同样生效）
    ctx_deny = _ctx(capability_modes={'hasn.community.create_post': 'deny'})
    deny_names = {t['name'] for t in (await directory.search(ctx_deny, query))['tools']}
    assert 'hasn.community.get_feed' in deny_names
    assert 'hasn.community.create_post' not in deny_names
