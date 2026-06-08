from __future__ import annotations

import json

from types import SimpleNamespace
from typing import Any

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.strings: dict[str, Any] = {}
        self.deleted: list[str] = []
        self.expired: list[tuple[str, int]] = []

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:  # noqa: ARG002
        self.strings[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self.strings else 0

    async def hset(self, key: str, field: str, value: Any) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key: str, field: str) -> Any:
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key: str, field: str) -> None:
        self.hashes.get(key, {}).pop(field, None)

    async def sadd(self, key: str, value: Any) -> None:
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key: str, value: Any) -> None:
        self.sets.get(key, set()).discard(value)

    async def smembers(self, key: str) -> set[Any]:
        return set(self.sets.get(key, set()))

    async def rpush(self, key: str, value: Any) -> None:
        self.lists.setdefault(key, []).append(value)

    async def lrange(self, key: str, start: int, stop: int) -> list[Any]:
        values = self.lists.get(key, [])
        if stop == -1:
            return values[start:]
        return values[start : stop + 1]

    async def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.hashes.pop(key, None)
        self.sets.pop(key, None)
        self.lists.pop(key, None)
        self.strings.pop(key, None)


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def send_text(self, value: str) -> None:
        if self.fail:
            raise RuntimeError('ws closed')
        self.sent.append(value)


class ScalarResult:
    def __init__(self, value: Any = None, values: list[Any] | None = None) -> None:
        self.value = value
        self.values = values or []

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> Any:
        values = self.values
        value = self.value

        class Scalars:
            def first(self) -> Any:
                return value

            def all(self) -> list[Any]:
                return values

        return Scalars()


class FakeDb:
    def __init__(self, results: list[ScalarResult]) -> None:
        self.results = results
        self.flush_count = 0

    async def execute(self, stmt: Any) -> ScalarResult:
        assert self.results
        return self.results.pop(0)

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_route_guard_uses_cache_db_and_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import route_guard as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)

    await redis.hset(
        'hasn:rel:h_a:h_b',
        'social',
        json.dumps({'trust_level': 2, 'status': 'connected'}),
    )
    assert await module.route_guard.check_permission(FakeDb([]), 'h_a', 'h_b') is True

    await redis.hset(
        'hasn:rel:h_blocked:h_b',
        'social',
        json.dumps({'trust_level': 0, 'status': 'blocked'}).encode(),
    )
    assert await module.route_guard.check_permission(FakeDb([]), 'h_blocked', 'h_b') is False

    relation = SimpleNamespace(trust_level=3, status='connected')
    assert await module.route_guard.check_permission(FakeDb([ScalarResult(value=relation)]), 'h_c', 'h_d') is True
    assert redis.hashes['hasn:rel:h_c:h_d']['social']
    assert redis.hashes['hasn:rel:h_d:h_c']['social']

    pending = SimpleNamespace(trust_level=1, status='pending')
    assert await module.route_guard.check_permission(FakeDb([ScalarResult(value=pending)]), 'h_e', 'h_f') is False
    assert await module.route_guard.check_permission(FakeDb([ScalarResult(value=None)]), 'h_g', 'h_h') is False

    await module.route_guard.invalidate_cache('h_c', 'h_d')
    assert redis.deleted[-2:] == ['hasn:rel:h_c:h_d', 'hasn:rel:h_d:h_c']


@pytest.mark.asyncio
async def test_ws_router_registration_owner_agent_and_push_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()

    router = module.WsRouterService()
    node_ws = FakeWebSocket()
    await router.register_node('node-1', 'desktop', node_ws, capacity=2)
    assert 'node-1' in module._ws_connections
    assert json.loads(redis.hashes[module.NODE_CONN_KEY]['node-1'])['capacity'] == 2

    binding = SimpleNamespace(
        binding_id='bind-1',
        scopes={'bind_owner': True},
        expires_at=SimpleNamespace(isoformat=lambda: '2026-05-18T00:00:00+00:00'),
    )
    monkeypatch.setattr(
        module.hasn_node_bindings_service,
        'add_owner_binding',
        lambda **kwargs: _async_value(binding),
    )
    owner_result = await router.add_owner(
        'node-1', 'h_owner', {'type': 'bearer_token'}, FakeDb([]), skip_proof_verify=True
    )
    assert owner_result['accepted'] is True
    assert redis.hashes[module.ENTITY_NODE_KEY]['h_owner'] == 'node-1'
    assert 'node-1' in redis.sets[f'{module.USER_NODES_PREFIX}:h_owner']

    active_binding = SimpleNamespace(binding_id='bind-2', expires_at=SimpleNamespace(isoformat=lambda: 'later'))
    monkeypatch.setattr(
        module.hasn_node_bindings_service,
        'get_active_binding',
        lambda **kwargs: _async_value(active_binding),
    )
    agent = SimpleNamespace(hasn_id='a_agent', owner_id='h_owner', status='active', node_id=None)
    add_agent = await router.add_agent_presence(
        'node-1',
        'a_agent',
        'h_owner',
        FakeDb([ScalarResult(value=agent), ScalarResult(value=agent)]),
    )
    assert add_agent == {'agent_id': 'a_agent', 'accepted': True}
    assert agent.node_id == 'node-1'

    pushed = await router.push_message_to('h_owner', {'created_time': '2', 'body': 'hi'})
    assert pushed is True
    assert json.loads(node_ws.sent[-1])['body'] == 'hi'

    agent_push = await router.push_message_to('a_agent', {'created_time': '1', 'body': 'agent'})
    assert agent_push is True

    module._ws_connections['node-1'] = FakeWebSocket(fail=True)
    queued = await router.push_message_to('a_agent', {'created_time': '3', 'body': 'queue'})
    assert queued is True
    assert redis.lists[f'{module.PUSH_PREFIX}:node-1']

    offline = await router.push_message_to('a_missing', {'created_time': '0', 'body': 'offline'})
    assert offline is False
    assert redis.lists[f'{module.OFFLINE_PREFIX}:a_missing']

    messages = await router.get_offline_messages(['a_missing'])
    assert messages == [{'created_time': '0', 'body': 'offline'}]
    assert f'{module.OFFLINE_PREFIX}:a_missing' in redis.deleted

    assert await router.is_human_online('h_owner') is True
    assert await router.is_agent_online('a_agent') is True
    assert await router.get_entity_status('h_owner') == 'online'
    assert await router.get_entity_status('a_missing') == 'offline'

    await router.remove_agent_presence('node-1', 'a_agent')
    assert 'a_agent' not in redis.hashes.get(module.ENTITY_NODE_KEY, {})

    await router.unregister_node('node-1')
    assert 'node-1' not in module._ws_connections
    assert f'{module.NODE_ENTITIES_PREFIX}:node-1' in redis.deleted


@pytest.mark.asyncio
async def test_ws_router_rejects_invalid_or_moved_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.WsRouterService()

    assert await router._validate_agent(
        'node-1', 'a_missing', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=None)])
    ) == {
        'hasn_id': 'a_missing',
        'reason': 'Agent 不存在',
    }

    wrong_owner = SimpleNamespace(hasn_id='a_agent', owner_id='h_other', status='active')
    err = await router._validate_agent(
        'node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=wrong_owner)])
    )
    assert err and 'owner_id 不匹配' in err['reason']

    stopped = SimpleNamespace(hasn_id='a_agent', owner_id='h_owner', status='disabled')
    assert await router._validate_agent(
        'node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=stopped)])
    ) == {
        'hasn_id': 'a_agent',
        'reason': 'Agent 已停用',
    }

    await redis.hset(module.ENTITY_NODE_KEY, 'a_agent', 'old-node')
    module._ws_connections['old-node'] = FakeWebSocket()
    active = SimpleNamespace(hasn_id='a_agent', owner_id='h_owner', status='active')
    err = await router._validate_agent(
        'node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=active)])
    )
    assert err and '已在节点 old-node 上运行' in err['reason']


def _async_value(value: Any):
    async def inner(**kwargs: Any) -> Any:
        return value

    return inner()
