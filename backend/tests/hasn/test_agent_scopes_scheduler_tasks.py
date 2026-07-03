from __future__ import annotations

import json

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.hasn.schema.agent_scopes import UpdateAgentScopesRequest


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[Any, Any]] = {}
        self.lists: dict[str, list[str]] = {}
        self.expired: list[tuple[str, int]] = []

    async def hgetall(self, key: str) -> dict[Any, Any]:
        return self.hashes.get(key, {})

    async def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    async def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))


@pytest.mark.asyncio
async def test_agent_scopes_service_get_update_and_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    """三态服务（D3）：get 透出 default_mode/capability_modes；update 走 update_agent_modes（不重签 JWT）。"""
    from backend.app.hasn.service import agent_scopes_service as module

    agent = SimpleNamespace(owner_id='h_owner', display_name='Agent One', agent_name='agent_one')

    async def get_by_hasn_id(db: object, hasn_id: str) -> object | None:  # noqa: RUF029
        assert hasn_id == 'a_agent'
        return agent

    # v3：判定真相只剩 default_mode + capability_modes（scopes/post_needs_review 列已 drop）。
    state: dict[str, object] = {
        'default_mode': 'allow',
        'capability_modes': {},
    }

    async def get_cached(agent_hasn_id: str, db: object) -> dict[str, object]:  # noqa: RUF029
        assert agent_hasn_id == 'a_agent'
        return dict(state)

    updated: dict[str, object] = {}

    async def update_modes(**kwargs: object) -> None:  # noqa: RUF029
        updated.update(kwargs)
        # 模拟写库后缓存最新值（D3：失效后下次现查）
        state['default_mode'] = kwargs['default_mode']
        state['capability_modes'] = kwargs['capability_modes']

    monkeypatch.setattr(module.hasn_agents_dao, 'get_by_hasn_id', get_by_hasn_id)
    monkeypatch.setattr(module, 'get_agent_scopes_cached', get_cached)
    monkeypatch.setattr(module, 'update_agent_modes', update_modes)

    service = module.AgentScopesService()
    config = await service.get_agent_scopes(object(), 'a_agent', 'h_owner')
    assert config.default_mode == 'allow'
    assert config.capability_modes == {}

    response = await service.update_agent_scopes(
        object(),
        'a_agent',
        'h_owner',
        UpdateAgentScopesRequest(
            default_mode='ask',
            capability_modes={'message:send': 'deny'},
        ),
    )
    # D3：写表参数命中 update_agent_modes，不签发 JWT。
    assert updated['default_mode'] == 'ask'
    assert updated['capability_modes'] == {'message:send': 'deny'}
    assert response.config.default_mode == 'ask'
    assert response.config.capability_modes == {'message:send': 'deny'}
    assert not hasattr(response, 'agent_token')

    with pytest.raises(Exception) as forbidden:
        await service.get_agent_scopes(object(), 'a_agent', 'h_other')
    assert forbidden.value.__class__.__name__ == 'ForbiddenError'

    async def missing_agent(db: object, hasn_id: str) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(module.hasn_agents_dao, 'get_by_hasn_id', missing_agent)
    with pytest.raises(Exception) as missing:
        await service.get_agent_scopes(object(), 'a_agent', 'h_owner')
    assert missing.value.__class__.__name__ == 'NotFoundError'


@pytest.mark.asyncio
async def test_agent_scopes_api_resolves_owner_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api import agent_scopes as module

    human = SimpleNamespace(hasn_id='h_owner')

    async def get_by_user_id(db: object, user_id: int) -> object | None:  # noqa: RUF029
        assert user_id == 7
        return human

    captured: list[tuple[str, dict[str, object]]] = []

    async def get_scopes(**kwargs: object) -> str:  # noqa: RUF029
        captured.append(('get', kwargs))
        return 'config'

    async def update_scopes(**kwargs: object) -> str:  # noqa: RUF029
        captured.append(('update', kwargs))
        return 'updated'

    async def get_catalog(**kwargs: object) -> str:  # noqa: RUF029
        captured.append(('catalog', kwargs))
        return 'catalog'

    monkeypatch.setattr('backend.app.hasn.api.agent_scopes.hasn_humans_dao.get_by_user_id', get_by_user_id)
    monkeypatch.setattr(module.agent_scopes_service, 'get_agent_scopes', get_scopes)
    monkeypatch.setattr(module.agent_scopes_service, 'update_agent_scopes', update_scopes)
    monkeypatch.setattr(module.agent_scopes_service, 'get_scope_catalog', get_catalog)

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    # API 一律走 fba 标准信封 ResponseModel（daemon envelope 解析依赖此约定）；
    # service 结果落在 .data，不再裸返回。
    get_resp = await module.get_agent_scopes(request, 'a_agent', object())
    assert get_resp.code == 200
    assert get_resp.data == 'config'
    update_resp = await module.update_agent_scopes(
        request,
        'a_agent',
        UpdateAgentScopesRequest(default_mode='deny', capability_modes={}),
        object(),
    )
    assert update_resp.data == 'updated'
    catalog_resp = await module.get_scope_catalog(request, 'a_agent', object())
    assert catalog_resp.data == 'catalog'

    # 身份从 Owner JWT 解析为 owner_hasn_id；D3 后不再透传 owner_user_id。
    assert captured[0][1]['owner_hasn_id'] == 'h_owner'
    assert 'owner_user_id' not in captured[1][1]
    assert captured[1][1]['owner_hasn_id'] == 'h_owner'
    assert captured[2][1]['owner_hasn_id'] == 'h_owner'

    async def missing_human(db: object, user_id: int) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr('backend.app.hasn.api.agent_scopes.hasn_humans_dao.get_by_user_id', missing_human)
    with pytest.raises(Exception) as exc_info:
        await module.get_agent_scopes(request, 'a_agent', object())
    assert exc_info.value.__class__.__name__ == 'NotFoundError'


@pytest.mark.asyncio
async def test_node_scheduler_selects_nodes_and_enqueues_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import node_scheduler as module

    redis = FakeRedis()
    redis.hashes[module.NODE_CONN_KEY] = {
        b'node-small': json.dumps({'node_type': 'cloud', 'capacity': 1}),
        'node-large': json.dumps({'node_type': 'cloud', 'capacity': 3}),
        'node-local': json.dumps({'node_type': 'desktop', 'capacity': 99}),
        'node-bad': '{bad json',
    }
    monkeypatch.setattr(module, 'redis_client', redis)

    scheduler = module.NodeSchedulerService()
    nodes = await scheduler.get_all_active_nodes()
    assert {node['node_id'] for node in nodes} == {'node-small', 'node-large', 'node-local'}
    assert await scheduler.select_optimal_node(node_type='cloud') == 'node-large'
    assert await scheduler.select_optimal_node(node_type='missing') is None

    assert await scheduler.provision_agent_to_node('a_agent', 'h_owner', 'node-large', {'x': 1}) is True
    queued = json.loads(redis.lists[f'{module.PUSH_PREFIX}:node-large'][0])
    assert queued == {
        'type': 'provision_agent',
        'data': {'agent_hasn_id': 'a_agent', 'owner_id': 'h_owner', 'config': {'x': 1}},
    }
    assert redis.expired[-1] == (f'{module.PUSH_PREFIX}:node-large', 3600)
