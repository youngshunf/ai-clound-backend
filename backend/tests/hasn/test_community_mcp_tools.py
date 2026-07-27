"""社区 Agent MCP 工具集（33 工具）一致性与入参校验单测。

零漂移守卫：manifest（运行时权威源）的每个社区工具，必须同时有
  - dispatch handler（community_tool_handlers.handle_community_*，或 get_post/get_article 走 service 直取）
  - 入参校验规则（_COMMUNITY_INPUT_RULES）
  - 已登记 scope（scopes.SCOPE_CATALOG）
并验证 _valid_tool_input 的必填/枚举/长度逻辑。活体行为见 scripts/mcp_e2e_community_tools.py。
"""

from __future__ import annotations

from backend.app.hasn.service.ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST
from backend.app.hasn.service.ai_native_runtime_gateway import (
    _COMMUNITY_INPUT_RULES,
    AiNativeRuntimeGateway,
)
from backend.app.hasn_community.service import community_tool_handlers as handlers
from backend.app.mcp.scopes import SCOPE_CATALOG

# 帖子/文章详情走 community_service 专用资源取数（含可见性鉴权），不经 handler。
_SERVICE_DIRECT = {'community.get_post', 'community.get_article'}
_EXPECTED_TOOLS = {
    'community.get_feed', 'community.get_post', 'community.get_article', 'community.get_comments',
    'community.search', 'community.discover_peers', 'community.get_profile', 'community.get_profile_content',
    'community.get_trending_topics', 'community.get_recommended_agents', 'community.get_notifications',
    'community.mark_notifications_read', 'community.create_post', 'community.create_article',
    'community.create_comment', 'community.like', 'community.unlike', 'community.follow',
    'community.unfollow', 'community.collect', 'community.uncollect',
    # 话题 / 圈子 / 文档系统（实施/95 §2.5；+12）
    'community.get_topic', 'community.get_topic_feed',
    'community.get_circle', 'community.get_circle_feed', 'community.discover_circles', 'community.list_my_circles',
    'community.join_circle', 'community.leave_circle',
    'community.get_doc_space', 'community.get_doc_tree', 'community.create_doc_space', 'community.create_doc_node',
}


def test_manifest_has_33_tools_and_capabilities() -> None:
    caps = COMMUNITY_AI_NATIVE_MANIFEST['capabilities']
    tools = COMMUNITY_AI_NATIVE_MANIFEST['tools']
    assert len(caps) == 33
    assert len(tools) == 33
    cap_ids = {c['tool_id'] for c in caps}
    tool_ids = {t['tool_id'] for t in tools}
    assert cap_ids == tool_ids == _EXPECTED_TOOLS


def test_every_tool_has_handler_validation_and_scope() -> None:
    """漂移守卫：每个 manifest 工具都有 dispatch handler + 校验规则 + 已登记 scope。"""
    for cap in COMMUNITY_AI_NATIVE_MANIFEST['capabilities']:
        tool_id = cap['tool_id']
        action = tool_id.split('.', 1)[1]
        # dispatch：service-direct 或 存在 handler 函数
        if tool_id not in _SERVICE_DIRECT:
            assert hasattr(handlers, f'handle_community_{action}'), f'缺少 handler: {action}'
        # 入参校验规则
        assert tool_id in _COMMUNITY_INPUT_RULES, f'缺少校验规则: {tool_id}'
        # scope 已登记
        for scope in cap['required_scopes']:
            assert scope in SCOPE_CATALOG, f'scope 未登记 SCOPE_CATALOG: {scope}'


def test_new_scopes_registered() -> None:
    for scope in ('community:comment', 'community:interact', 'community:circle', 'community:doc'):
        assert scope in SCOPE_CATALOG
        assert SCOPE_CATALOG[scope]['domain'] == 'community'


def test_scope_grouping_matches_design() -> None:
    """4 个 colon scope 的覆盖与设计 14-doc §3 一致。"""
    by_scope: dict[str, set[str]] = {}
    for cap in COMMUNITY_AI_NATIVE_MANIFEST['capabilities']:
        for scope in cap['required_scopes']:
            by_scope.setdefault(scope, set()).add(cap['tool_id'])
    assert by_scope['community:post'] == {'community.create_post', 'community.create_article'}
    assert by_scope['community:comment'] == {'community.create_comment'}
    assert by_scope['community:interact'] == {
        'community.like', 'community.unlike', 'community.follow',
        'community.unfollow', 'community.collect', 'community.uncollect',
    }
    assert by_scope['community:circle'] == {'community.join_circle', 'community.leave_circle'}
    assert by_scope['community:doc'] == {'community.create_doc_space', 'community.create_doc_node'}
    # 原 11 读 + 发现同伴1 + 话题2 + 圈子读4 + 文档读2 = 20
    assert len(by_scope['community:read']) == 20


def test_valid_tool_input_create_comment() -> None:
    gw = AiNativeRuntimeGateway()
    ok = {'target_type': 'post', 'target_id': 'p_1', 'content': '内容'}
    assert gw._valid_tool_input('community.create_comment', ok) is True
    assert gw._valid_tool_input('community.create_comment', {'target_type': 'post', 'target_id': 'p_1'}) is False  # 缺 content
    assert gw._valid_tool_input('community.create_comment', {**ok, 'target_type': 'user'}) is False  # 枚举不符
    assert gw._valid_tool_input('community.create_comment', {**ok, 'content': 'x' * 5001}) is False  # 超长


def test_valid_tool_input_interact_and_read() -> None:
    gw = AiNativeRuntimeGateway()
    assert gw._valid_tool_input('community.like', {'target_type': 'post', 'target_id': 'p_1'}) is True
    assert gw._valid_tool_input('community.like', {'target_type': 'topic', 'target_id': 'p_1'}) is False  # like 不含 topic
    assert gw._valid_tool_input('community.follow', {'target_type': 'topic', 'target_id': 't_1'}) is True
    assert gw._valid_tool_input('community.search', {'query': 'AI'}) is True
    assert gw._valid_tool_input('community.search', {'query': '   '}) is False  # 空查询
    assert gw._valid_tool_input('community.get_profile_content', {'hasn_id': 'h_1', 'kind': 'posts'}) is True
    assert gw._valid_tool_input('community.get_profile_content', {'hasn_id': 'h_1', 'kind': 'bad'}) is False
    assert gw._valid_tool_input('community.discover_peers', {}) is True
    assert gw._valid_tool_input('community.discover_peers', {'peer_type': 'agent', 'limit': 12}) is True
    assert gw._valid_tool_input('community.discover_peers', {'peer_type': 'unknown'}) is False
    # 无参读取工具
    assert gw._valid_tool_input('community.get_trending_topics', {}) is True
    assert gw._valid_tool_input('community.get_trending_topics', {'limit': 999}) is False  # limit 越界
