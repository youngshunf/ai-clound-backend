"""HASN Agent Profile cloud-first create / sync contract tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest


@dataclass
class _Template:
    template_id: str = 'tpl_assistant'
    name: str = '助理模板'
    avatar: str = 'https://cdn.example.com/default.png'
    default_skills: dict[str, Any] = field(default_factory=lambda: {'enabled': ['chat', 'calendar']})
    default_soul_md: str = '# 默认 SOUL\n你是一个靠谱助理。'
    default_user_md: str = '# 默认 USER\n用户偏好简洁。'
    default_description: str = '默认简介'
    default_runtime_type: str = 'hermes'


@dataclass
class _Agent:
    id: int = 1
    hasn_id: str = 'a_created'
    star_id: str = '100001#agent'
    owner_id: str = 'h_owner'
    agent_name: str = 'agent'
    display_name: str = '云端 Agent'
    description: str | None = '用户简介'
    avatar: str | None = 'https://cdn.example.com/user.png'
    type: str = 'desktop'
    role: str = 'specialist'
    node_id: str | None = 'n_local'
    capabilities: dict[str, Any] | None = None
    template_id: str | None = 'tpl_assistant'
    skills: dict[str, Any] | None = field(default_factory=lambda: {'enabled': ['chat']})
    soul_md: str | None = '# 用户 SOUL\n按我的方式工作。'
    user_md: str | None = '# 用户 USER\n我是福仔。'
    profile_revision: int = 1
    status: str = 'active'
    created_via: str = 'client'
    created_time: datetime = datetime(2026, 5, 1, tzinfo=timezone.utc)
    updated_time: datetime | None = None


class _FakeDB:
    """sync_agents 现会查公共技能集合修订号；纯快照映射单测无真实库 ⇒ 返回空公共集合（revision '0'）。"""

    class _Result:
        @staticmethod
        def all() -> list:
            return []

    async def execute(self, *_a: Any, **_k: Any) -> '_FakeDB._Result':
        return _FakeDB._Result()


class _Gateway:
    def __init__(self) -> None:
        self.created_payload: dict[str, Any] | None = None
        self.sync_events: list[dict[str, Any]] = []
        self.templates = {'tpl_assistant': _Template()}
        self.agents = [_Agent()]

    async def owns_owner(self, _db: Any, *, owner_id: str, user_id: int) -> bool:
        return owner_id == 'h_owner' and user_id == 100

    async def get_template(self, _db: Any, *, template_id: str) -> _Template | None:
        return self.templates.get(template_id)

    async def create_agent(self, _db: Any, payload: dict[str, Any]) -> tuple[_Agent, str | None, bool]:
        self.created_payload = payload
        agent = _Agent(
            agent_name=payload['agent_name'],
            display_name=payload['display_name'],
            description=payload['description'],
            avatar=payload['avatar'],
            skills=payload['skills'],
            soul_md=payload['soul_md'],
            user_md=payload['user_md'],
            template_id=payload['template_id'],
            node_id=payload['node_id'],
        )
        self.agents = [agent]
        return agent, 'hasn_ak_test', False

    async def list_owner_agents(self, _db: Any, *, owner_id: str, after_revision: int | None = None) -> list[_Agent]:
        return [agent for agent in self.agents if agent.owner_id == owner_id and agent.profile_revision > (after_revision or 0)]

    async def append_agent_sync_event(self, _db: Any, *, owner_id: str, agent: _Agent, event_type: str) -> None:
        self.sync_events.append({'owner_id': owner_id, 'agent_id': agent.hasn_id, 'event_type': event_type})


@pytest.mark.asyncio
async def test_cloud_first_create_merges_template_defaults_and_returns_agent_snapshot() -> None:
    from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    gateway = _Gateway()
    service = HasnAgentProfileService(gateway=gateway)

    response = await service.create_cloud_first(
        db=None,
        request=CloudCreateAgentRequest(
            owner_id='h_owner',
            template_id='tpl_assistant',
            agent_name=None,
            display_name='云端 Agent',
            description='用户简介',
            avatar='https://cdn.example.com/user.png',
            skills={'enabled': ['chat']},
            soul_md='# 用户 SOUL\n按我的方式工作。',
            user_md='# 用户 USER\n我是福仔。',
            runtime_type='hermes',
            node_id='n_local',
        ),
        user_id=100,
    )

    expected_core_payload = {
        'owner_id': 'h_owner',
        'template_id': 'tpl_assistant',
        'agent_name': 'tpl_assistant',
        'display_name': '云端 Agent',
        'description': '用户简介',
        'avatar': 'https://cdn.example.com/user.png',
        'skills': {'enabled': ['chat']},
        'soul_md': '# 用户 SOUL\n按我的方式工作。',
        'user_md': '# 用户 USER\n我是福仔。',
        'runtime_type': 'hermes',
        'node_id': 'n_local',
    }
    assert gateway.created_payload is not None
    for key, value in expected_core_payload.items():
        assert gateway.created_payload[key] == value
    assert response.agent.hasn_id == 'a_created'
    assert response.agent.display_name == '云端 Agent'
    assert response.agent.avatar == 'https://cdn.example.com/user.png'
    assert response.agent.description == '用户简介'
    assert response.agent.template_id == 'tpl_assistant'
    assert response.agent.skills == {'enabled': ['chat']}
    assert response.agent.soul_md.startswith('# 用户 SOUL')
    assert response.agent.user_md.startswith('# 用户 USER')
    assert response.agent.profile_revision == 1
    assert response.agent_key == 'hasn_ak_test'
    assert response.already_exists is False
    assert gateway.sync_events == [{'owner_id': 'h_owner', 'agent_id': 'a_created', 'event_type': 'agent.created'}]


@pytest.mark.asyncio
async def test_cloud_first_create_uses_template_defaults_when_user_omits_optional_profile() -> None:
    from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    gateway = _Gateway()
    service = HasnAgentProfileService(gateway=gateway)

    response = await service.create_cloud_first(
        db=None,
        request=CloudCreateAgentRequest(
            owner_id='h_owner',
            template_id='tpl_assistant',
            agent_name=None,
            display_name='云端 Agent',
        ),
        user_id=100,
    )

    assert gateway.created_payload is not None
    assert gateway.created_payload['description'] == '默认简介'
    assert gateway.created_payload['avatar'] == 'https://cdn.example.com/default.png'
    assert gateway.created_payload['skills'] == {'enabled': ['chat', 'calendar']}
    assert gateway.created_payload['soul_md'].startswith('# 默认 SOUL')
    assert gateway.created_payload['user_md'].startswith('# 默认 USER')
    assert gateway.created_payload['runtime_type'] == 'hermes'
    assert response.agent.description == '默认简介'


@pytest.mark.asyncio
async def test_sync_agents_returns_latest_cloud_agent_snapshots() -> None:
    from backend.app.hasn.schema.hasn_agents import AgentSyncRequest
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    gateway = _Gateway()
    gateway.agents[0].display_name = '云端最新昵称'
    gateway.agents[0].avatar = 'https://cdn.example.com/latest.png'
    gateway.agents[0].description = '云端最新简介'
    gateway.agents[0].profile_revision = 7
    service = HasnAgentProfileService(gateway=gateway)

    response = await service.sync_agents(
        db=_FakeDB(),
        request=AgentSyncRequest(owner_id='h_owner', after_revision=3),
        user_id=100,
    )

    assert response.owner_id == 'h_owner'
    assert response.server_revision == 7
    assert len(response.agents) == 1
    snapshot = response.agents[0]
    assert snapshot.display_name == '云端最新昵称'
    assert snapshot.avatar == 'https://cdn.example.com/latest.png'
    assert snapshot.description == '云端最新简介'
    assert snapshot.profile_revision == 7
    assert snapshot.agent_name == 'agent'
    assert not hasattr(snapshot, 'nickname')


@pytest.mark.asyncio
async def test_sync_agents_without_after_revision_returns_full_authoritative_set() -> None:
    from backend.app.hasn.schema.hasn_agents import AgentSyncRequest
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    gateway = _Gateway()
    gateway.agents = [
        _Agent(
            id=1,
            hasn_id='a_old_revision',
            agent_name='old-slug',
            display_name='旧版本云端展示名',
            avatar='https://cdn.example.com/old.png',
            profile_revision=1,
        ),
        _Agent(
            id=2,
            hasn_id='a_new_revision',
            agent_name='new-slug',
            display_name='新版本云端展示名',
            avatar='https://cdn.example.com/new.png',
            profile_revision=9,
        ),
    ]
    service = HasnAgentProfileService(gateway=gateway)

    response = await service.sync_agents(
        db=_FakeDB(),
        request=AgentSyncRequest(owner_id='h_owner', after_revision=None),
        user_id=100,
    )

    assert response.server_revision == 9
    assert [agent.hasn_id for agent in response.agents] == ['a_old_revision', 'a_new_revision']
    assert [agent.display_name for agent in response.agents] == ['旧版本云端展示名', '新版本云端展示名']
    assert [agent.avatar for agent in response.agents] == [
        'https://cdn.example.com/old.png',
        'https://cdn.example.com/new.png',
    ]
    assert [agent.agent_name for agent in response.agents] == ['old-slug', 'new-slug']
