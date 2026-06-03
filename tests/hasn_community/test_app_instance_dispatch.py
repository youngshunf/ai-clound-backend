"""设计 14-AI-Native应用平台/11 —— P1+P2 真实联调（连真实 PG，事务回滚隔离，零 Mock 零 Fake）。

覆盖：
- InstanceResolver.resolve 对内置工具返回 gateway_internal 句柄（endpoint=云端自身）
- _dispatch_tool 去硬编码：经 instance_resolver + internal_handlers 注册表真实路由社区 feed（不再 `if app_id==`）
- 去掉安装态：无 workspace_app 记录默认可用；仅企业 override status=disabled 时不可用
- 未知 transport → 15052；cloud_relay 尚未配置实例 → 15050（P3 落地真实签名转发）
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.hasn.model import HasnWorkspaceApp
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway as gateway
from backend.app.hasn.service.instance_resolver import (
    FACE_TOOL,
    TRANSPORT_GATEWAY_INTERNAL,
    InstanceResolutionError,
    instance_resolver,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.utils.timezone import timezone
from tests.hasn_community.conftest import seed_agent, seed_human, seed_post


def _agent_payload(owner: dict, agent_row: dict) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=agent_row['hasn_id'],
        agent_name=agent_row['display_name'],
        owner_hasn_id=owner['hasn_id'],
        owner_user_id=owner['user_id'],
        scopes=['community:read', 'community:post', 'community:comment', 'community:interact'],
        session_uuid='sess-test-app-instance',
        expire_time=timezone.now() + timedelta(hours=1),
    )


def _personal_ws(owner: dict) -> dict:
    return {
        'kind': 'personal',
        'user_id': owner['user_id'],
        'enterprise_id': None,
        'workspace_key': f"personal:{owner['user_id']}",
    }


@pytest.mark.asyncio
async def test_resolve_builtin_tool_returns_gateway_internal_handle(db):
    """内置工具解析为 gateway_internal：endpoint=云端自身、无外部凭据（设计 11 §2.2/§3.1）。"""
    manifest = await ai_native_app_registry.ensure_builtin_published(db, 'community')
    handle = await instance_resolver.resolve(
        db, app_id='community', workspace={'kind': 'personal'},
        face=FACE_TOOL, manifest=manifest, tool_id='community.get_feed',
    )
    assert handle.transport == TRANSPORT_GATEWAY_INTERNAL
    assert handle.is_internal is True
    assert handle.endpoint is None
    assert handle.credential is None


@pytest.mark.asyncio
async def test_dispatch_community_feed_via_registry_not_hardcoded(db):
    """_dispatch_tool 经注册表真实路由社区 feed（消灭 `if app_id==` 硬编码），返回真实数据。

    这是"Agent 调用工具出错"的直接修复点：去硬编码后内置工具仍全链路调通。
    """
    owner = await seed_human(db, nickname='实例主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='实例分身')
    post_id = await seed_post(db, author_hasn_id=owner['hasn_id'], content='主人的帖子（recommend 流应可见）')

    manifest = await ai_native_app_registry.ensure_builtin_published(db, 'community')
    result = await gateway._dispatch_tool(
        db,
        app_id='community',
        tool_id='community.get_feed',
        workspace=_personal_ws(owner),
        agent=_agent_payload(owner, agent_row),
        input_payload={'type': 'recommend', 'limit': 20},
        manifest=manifest,
    )
    # 经注册表路由到真实 handle_community_get_feed → community_service.get_feed，返回真实契约形状
    # （'posts'/'cursor'/'has_more'），证明去硬编码后内置工具仍真实取数（非 stub / 非空填充）。
    assert isinstance(result, dict)
    assert isinstance(result.get('posts'), list)
    assert 'has_more' in result and 'cursor' in result
    assert result['posts'], 'recommend 流应返回真实帖子（证明去硬编码后真实路由生效）'
    _ = post_id  # 已落库真实帖子；recommend 排序不保证置顶，断言真实契约即可


@pytest.mark.asyncio
async def test_internal_handler_registry_covers_knowledge_and_community(db):
    """handler 注册表覆盖知识库 + 社区全量工具（取代两套硬编码分支）。"""
    registry = gateway._internal_handlers()
    assert 'knowledge.search' in registry and callable(registry['knowledge.search'])
    for tool_id in ('community.get_feed', 'community.get_post', 'community.create_post', 'community.create_doc_node'):
        assert tool_id in registry and callable(registry[tool_id]), tool_id


@pytest.mark.asyncio
async def test_workspace_app_available_by_default_without_record(db):
    """去掉安装态：个人空间无 workspace_app 记录即默认可用（published 即用，设计 11 §4.2）。"""
    owner = await seed_human(db, nickname='零配置主人')
    available = await gateway._is_workspace_app_available(
        db, workspace=_personal_ws(owner), app_id='community'
    )
    assert available is True


@pytest.mark.asyncio
async def test_workspace_app_enterprise_override_disabled_blocks(db):
    """企业 override status=disabled → 不可用；status=active / 无记录 → 可用（设计 11 §4.2/§4.3）。"""
    ent_disabled = 990001
    ent_active = 990002
    db.add(HasnWorkspaceApp(workspace_kind='enterprise', enterprise_id=ent_disabled, app_id='community', status='disabled'))
    db.add(HasnWorkspaceApp(workspace_kind='enterprise', enterprise_id=ent_active, app_id='community', status='active'))
    await db.flush()

    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': ent_disabled}, app_id='community'
    ) is False
    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': ent_active}, app_id='community'
    ) is True
    # 无记录的企业空间同样回落为可用
    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': 990999}, app_id='community'
    ) is True


@pytest.mark.asyncio
async def test_resolve_unknown_transport_raises_15052(db):
    """manifest 声明非法 transport → 15052（该 transport 不允许此调用面）。"""
    fake = {'app_id': 'x', 'manifest_json': {'tools': [{'tool_id': 'x.do', 'transport': 'bogus', 'handler': 'x.do'}]}}
    with pytest.raises(InstanceResolutionError) as ei:
        await instance_resolver.resolve(
            db, app_id='x', workspace={'kind': 'personal'}, face=FACE_TOOL, manifest=fake, tool_id='x.do'
        )
    assert ei.value.code == '15052'


@pytest.mark.asyncio
async def test_dispatch_cloud_relay_without_instance_raises_15050(db):
    """cloud_relay 工具但尚无实例行 → 15050（实例未配置）；证明第三方 transport 被识别，P3 落地真实转发。"""
    owner = await seed_human(db, nickname='第三方主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='第三方分身')
    fake = {
        'app_id': 'faketp',
        'manifest_json': {
            'tools': [{'tool_id': 'faketp.do', 'transport': 'cloud_relay', 'handler': 'faketp.do'}],
            'capabilities': [],
        },
    }
    with pytest.raises(InstanceResolutionError) as ei:
        await gateway._dispatch_tool(
            db,
            app_id='faketp',
            tool_id='faketp.do',
            workspace=_personal_ws(owner),
            agent=_agent_payload(owner, agent_row),
            input_payload={},
            manifest=fake,
        )
    assert ei.value.code == '15050'
