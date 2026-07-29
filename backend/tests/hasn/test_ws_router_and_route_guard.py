from __future__ import annotations

import json
import builtins

from types import SimpleNamespace
from typing import Any

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, builtins.set[Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.strings: dict[str, Any] = {}
        self.deleted: list[str] = []
        self.expired: list[tuple[str, int]] = []

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
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

    async def smembers(self, key: str) -> builtins.set[Any]:
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

    async def eval(self, _script: str, numkeys: int, *args: Any) -> int:
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if numkeys == 2:
            generation_key, alive_key = keys
            node_id, connection_id, _ttl = argv
            if self.hashes.get(generation_key, {}).get(node_id) != connection_id:
                return 0
            self.strings[alive_key] = '1'
            return 1
        if numkeys == 5:
            generation_key, node_conn_key, entities_key, entity_node_key, alive_key = keys
            node_id, connection_id, user_nodes_prefix = argv
            if self.hashes.get(generation_key, {}).get(node_id) != connection_id:
                return 0
            for hasn_id in list(self.sets.get(entities_key, set())):
                if self.hashes.get(entity_node_key, {}).get(hasn_id) == node_id:
                    self.hashes.get(entity_node_key, {}).pop(hasn_id, None)
                if str(hasn_id).startswith('h_'):
                    self.sets.get(f'{user_nodes_prefix}:{hasn_id}', set()).discard(node_id)
            await self.delete(entities_key)
            self.hashes.get(node_conn_key, {}).pop(node_id, None)
            await self.delete(alive_key)
            self.hashes.get(generation_key, {}).pop(node_id, None)
            return 1
        raise AssertionError(f'unexpected eval numkeys={numkeys}')


class _FakeWebSocket:
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


FakeWebSocket: Any = _FakeWebSocket


class _FakeDb:
    def __init__(self, results: list[ScalarResult]) -> None:
        self.results = results
        self.flush_count = 0

    async def execute(self, stmt: Any) -> ScalarResult:
        assert self.results
        return self.results.pop(0)

    async def flush(self) -> None:
        self.flush_count += 1


FakeDb: Any = _FakeDb


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
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()

    router = module.NodeSessionService()
    fake_node_ws = FakeWebSocket()
    node_ws: Any = fake_node_ws
    connection_id = await router.register_node('node-1', 'desktop', node_ws, capacity=2)
    module._ws_ready_connection_ids['node-1'] = connection_id
    assert module._ws_connections['node-1'] is node_ws
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
    # 在线语义收紧：路由注册后还需 daemon 心跳报 online+ok 才写就绪键 → 才算真在线。
    # 这一步等价于折叠心跳 `_handle_add_agent` 里的 set_agent_readiness 调用。
    await router.set_agent_readiness('a_agent', 'online', 'ok')

    pushed = await router.push_message_to('h_owner', {'created_time': '2', 'body': 'hi'})
    assert pushed is True
    assert json.loads(fake_node_ws.sent[-1])['body'] == 'hi'

    agent_push = await router.push_message_to('a_agent', {'created_time': '1', 'body': 'agent'})
    assert agent_push is True

    module._ws_connections['node-1'] = FakeWebSocket(fail=True)
    # 本地连接发送失败 → 退回投递总线（跨 worker），不再写已废弃的 PUSH_PREFIX 死队列
    bus_published: list[tuple[str, str]] = []

    async def _spy_bus(node_id: str, payload_json: str) -> bool:  # noqa: RUF029
        bus_published.append((node_id, payload_json))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy_bus)
    queued = await router.push_message_to('a_agent', {'created_time': '3', 'body': 'queue'})
    assert queued is True
    assert any(n == 'node-1' for n, _ in bus_published)
    assert f'{module.PUSH_PREFIX}:node-1' not in redis.lists  # 旧死队列不再写

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

    await router.unregister_node('node-1', module._ws_connection_ids['node-1'])
    assert 'node-1' not in module._ws_connections
    assert f'{module.NODE_ENTITIES_PREFIX}:node-1' in redis.deleted


@pytest.mark.asyncio
async def test_stale_connection_cleanup_cannot_remove_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 node 重连后，旧 handler 的 finally 只能清理自己的连接代际。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.NodeSessionService()

    old_ws = FakeWebSocket()
    new_ws = FakeWebSocket()
    old_connection_id = await router.register_node('node-overlap', 'desktop', old_ws)
    await router._register_entity('node-overlap', 'h_owner', is_human=True)
    new_connection_id = await router.register_node('node-overlap', 'desktop', new_ws)
    await router._register_entity('node-overlap', 'h_owner', is_human=True)

    assert old_connection_id != new_connection_id
    await router.unregister_node('node-overlap', old_connection_id)

    assert module._ws_connections['node-overlap'] is new_ws
    assert redis.hashes[module.NODE_GENERATION_KEY]['node-overlap'] == new_connection_id
    assert redis.hashes[module.ENTITY_NODE_KEY]['h_owner'] == 'node-overlap'
    assert 'node-overlap' in redis.sets[f'{module.USER_NODES_PREFIX}:h_owner']
    assert f'{module.NODE_ALIVE_PREFIX}:node-overlap' in redis.strings


@pytest.mark.asyncio
async def test_stale_connection_heartbeat_cannot_refresh_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    """被新连接取代的旧 socket 不得继续用 ping 维持在线假象。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.NodeSessionService()

    old_connection_id = await router.register_node('node-overlap', 'desktop', FakeWebSocket())
    new_connection_id = await router.register_node('node-overlap', 'desktop', FakeWebSocket())
    redis.strings.pop(f'{module.NODE_ALIVE_PREFIX}:node-overlap')

    assert await router.refresh_node_presence('node-overlap', old_connection_id) is False
    assert f'{module.NODE_ALIVE_PREFIX}:node-overlap' not in redis.strings
    assert await router.refresh_node_presence('node-overlap', new_connection_id) is True
    assert f'{module.NODE_ALIVE_PREFIX}:node-overlap' in redis.strings


@pytest.mark.asyncio
async def test_business_frames_wait_until_current_connection_handshake_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hasn.connected 前只入持久队列，握手 ready 后当前代际才允许直发。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    module._ws_connection_ids.clear()
    module._ws_ready_connection_ids.clear()
    router = module.NodeSessionService()
    ws = FakeWebSocket()
    connection_id = await router.register_node('node-ready', 'desktop', ws)
    published: list[tuple[str, str]] = []
    drained: list[str] = []

    async def _publish(node_id: str, payload_json: str) -> bool:  # noqa: RUF029
        published.append((node_id, payload_json))
        return True

    async def _drain(node_id: str) -> int:  # noqa: RUF029
        drained.append(node_id)
        return 0

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _publish)
    monkeypatch.setattr(module.ws_delivery_bus, 'drain_node', _drain)

    assert await router._send_or_publish('node-ready', 'BEFORE') is True
    assert ws.sent == []
    assert published == [('node-ready', 'BEFORE')]

    assert await router.mark_node_ready('node-ready', connection_id) is True
    assert drained == ['node-ready']
    assert await router._send_or_publish('node-ready', 'AFTER') is True
    assert ws.sent == ['AFTER']


@pytest.mark.asyncio
async def test_stale_worker_local_socket_is_not_used_for_direct_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 worker 重叠连接时，Redis 中非当前代际的本地 socket 只能绕到持久总线。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    stale_ws = FakeWebSocket()
    module._ws_connections.clear()
    module._ws_connection_ids.clear()
    module._ws_ready_connection_ids.clear()
    module._ws_connections['node-stale'] = stale_ws
    module._ws_connection_ids['node-stale'] = 'conn-old'
    module._ws_ready_connection_ids['node-stale'] = 'conn-old'
    redis.hashes[module.NODE_GENERATION_KEY] = {'node-stale': 'conn-new'}
    published: list[tuple[str, str]] = []

    async def _publish(node_id: str, payload_json: str) -> bool:  # noqa: RUF029
        published.append((node_id, payload_json))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _publish)

    assert await module.NodeSessionService()._send_or_publish('node-stale', 'MSG') is True
    assert stale_ws.sent == []
    assert published == [('node-stale', 'MSG')]


@pytest.mark.asyncio
async def test_ws_router_rejects_invalid_or_moved_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.NodeSessionService()

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

    # 接管权威改由 Redis 就绪判据裁定（node_alive ∧ agent_ready），不再看 per-worker WS 连接。
    active = SimpleNamespace(hasn_id='a_agent', owner_id='h_owner', status='active')

    # 场景①：旧持有者「就绪服务中」（node_alive + agent_ready 都在）→ 拒绝新节点接管。
    await redis.hset(module.ENTITY_NODE_KEY, 'a_agent', 'old-node')
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:old-node', '1')
    await redis.set(f'{module.AGENT_READY_PREFIX}:a_agent', '1')
    # WS 连接存在与否已不影响判定——即便连接挂了，只要就绪键在就仍受保护。
    module._ws_connections['old-node'] = FakeWebSocket()
    err = await router._validate_agent(
        'node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=active)])
    )
    assert err and '已在节点 old-node 上运行' in err['reason']

    # 场景②：旧持有者「降级未就绪」（node_alive 在、但 agent_ready 缺失）→ 允许新节点接管，
    # 并释放其残留路由。这正是「降级设备霸占路由致换设备接不了管」的夹缝，C1 修复点。
    await redis.hset(module.ENTITY_NODE_KEY, 'a_agent', 'old-node')
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:old-node', '1')
    await redis.delete(f'{module.AGENT_READY_PREFIX}:a_agent')
    module._ws_connections['old-node'] = FakeWebSocket()  # WS 仍连着也不再保护降级持有者
    assert (
        await router._validate_agent('node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=active)]))
        is None
    )
    assert await redis.hget(module.ENTITY_NODE_KEY, 'a_agent') is None  # 残留路由已被释放

    # 场景③：旧节点「已死」（node_alive 缺失）→ 允许新节点接管（僵尸路由回收）。
    await redis.hset(module.ENTITY_NODE_KEY, 'a_agent', 'dead-node')
    await redis.set(f'{module.AGENT_READY_PREFIX}:a_agent', '1')  # 就绪键残留但节点心跳已过期
    assert (
        await router._validate_agent('node-1', 'a_agent', {'owner_id': 'h_owner'}, FakeDb([ScalarResult(value=active)]))
        is None
    )
    assert await redis.hget(module.ENTITY_NODE_KEY, 'a_agent') is None


def _async_value(value: Any) -> Any:
    async def inner(**kwargs: Any) -> Any:  # noqa: RUF029
        return value

    return inner()
