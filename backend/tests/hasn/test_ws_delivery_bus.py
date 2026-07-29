"""MWORKER：跨 worker WebSocket 投递总线 + ws_router 投递路由。

根因回归：多 worker（``--workers N``）部署下 ``_ws_connections`` 仅含本进程连接，
旧实现把投不到本地连接的帧 ``rpush hasn:push:{node_id}`` 进无人消费的死队列 →
「完全收不到消息」。新实现经 Redis pub/sub 投递总线 fan-out 到持有连接的 worker。

与 test_sync_invalidate.py 同构：纯 fake（redis/ws），确定性模拟 publish→deliver 全环。
"""

from __future__ import annotations

import json

from typing import Any

import pytest


class _FakeWS:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def send_text(self, value: str) -> None:
        if self.fail:
            raise RuntimeError('ws closed')
        self.sent.append(value)

    async def send_json(self, value: dict) -> None:
        if self.fail:
            raise RuntimeError('ws closed')
        self.sent.append(json.dumps(value, ensure_ascii=False))


FakeWS: Any = _FakeWS


class FakeRedis:
    def __init__(self, *, fail_publish: bool = False) -> None:
        self.sets: dict[str, set[Any]] = {}
        self.hash: dict[str, dict[str, Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.published: list[tuple[str, str]] = []
        self.fail_publish = fail_publish

    async def smembers(self, key: str) -> set[Any]:
        return set(self.sets.get(key, set()))

    async def hget(self, key: str, field: str) -> Any:
        return self.hash.get(key, {}).get(field)

    async def rpush(self, key: str, value: Any) -> None:
        self.lists.setdefault(key, []).append(value)

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        values = self.lists.get(key, [])
        return list(values[start:] if end == -1 else values[start : end + 1])

    async def eval(self, _script: str, numkeys: int, *args: Any) -> Any:
        if numkeys == 2:
            source = str(args[0])
            destination = str(args[1])
            source_values = self.lists.setdefault(source, [])
            if not source_values:
                return None
            value = source_values.pop(0)
            self.lists.setdefault(destination, []).append(value)
            return value

        assert numkeys == 1
        key = str(args[0])
        claimed = list(args[1:])
        values = self.lists.setdefault(key, [])
        if values[: len(claimed)] != claimed:
            return 0
        del values[: len(claimed)]
        return len(claimed)

    async def rpoplpush(self, source: str, destination: str) -> Any | None:
        source_values = self.lists.setdefault(source, [])
        if not source_values:
            return None
        value = source_values.pop()
        self.lists.setdefault(destination, []).insert(0, value)
        return value

    async def lmove(self, source: str, destination: str, _wherefrom: str, _whereto: str) -> Any | None:
        source_values = self.lists.setdefault(source, [])
        if not source_values:
            return None
        value = source_values.pop(0)
        self.lists.setdefault(destination, []).append(value)
        return value

    async def lrem(self, key: str, count: int, value: Any) -> int:
        values = self.lists.setdefault(key, [])
        removed = 0
        retained: list[Any] = []
        for item in values:
            if item == value and removed < count:
                removed += 1
            else:
                retained.append(item)
        self.lists[key] = retained
        return removed

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def publish(self, channel: str, message: str) -> int:
        if self.fail_publish:
            raise RuntimeError('pubsub unavailable')
        self.published.append((channel, message))
        return 1


# ─────────────────────── 投递总线 _deliver_local 路由 ───────────────────────


@pytest.mark.asyncio
async def test_deliver_local_only_to_held_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """本 worker 持有该 node → 下发；不持有 → 无操作（不误投其它本地连接）。"""
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()
    ws_held = FakeWS()
    ws_other = FakeWS()
    rmod._ws_connections['node-held'] = ws_held
    rmod._ws_connections['node-other'] = ws_other
    rmod._ws_connection_ids['node-held'] = 'conn-held'
    rmod._ws_connection_ids['node-other'] = 'conn-other'
    rmod._ws_ready_connection_ids['node-held'] = 'conn-held'
    rmod._ws_ready_connection_ids['node-other'] = 'conn-other'
    redis.hash[rmod.NODE_GENERATION_KEY] = {
        'node-held': 'conn-held',
        'node-other': 'conn-other',
    }
    redis.lists[f'{busmod.PENDING_PREFIX}:node-held'] = ['P1']

    # 投给本 worker 持有的 node-held → 仅它收到
    await busmod.WsDeliveryBus._deliver_local({'node_id': 'node-held'})
    assert ws_held.sent == ['P1']
    assert ws_other.sent == []

    # 投给本 worker 不持有的 node-remote → 本 worker 无人下发（交给持有它的 worker）
    await busmod.WsDeliveryBus._deliver_local({'node_id': 'node-remote'})
    assert ws_held.sent == ['P1']
    assert ws_other.sent == []

    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()


@pytest.mark.asyncio
async def test_deliver_local_broadcast_only_hits_current_ready_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()
    ws_a, ws_b = FakeWS(), FakeWS()
    rmod._ws_connections['node-a'] = ws_a
    rmod._ws_connections['node-b'] = ws_b
    rmod._ws_connection_ids['node-a'] = 'conn-a'
    rmod._ws_connection_ids['node-b'] = 'conn-b'
    rmod._ws_ready_connection_ids['node-a'] = 'conn-a'
    redis.hash[rmod.NODE_GENERATION_KEY] = {
        'node-a': 'conn-a',
        'node-b': 'conn-b',
    }

    await busmod.WsDeliveryBus._deliver_local({'broadcast': True, 'payload': 'B'})
    assert ws_a.sent == ['B']
    assert ws_b.sent == []

    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()


@pytest.mark.asyncio
async def test_targeted_queue_waits_for_connected_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接已注册但未 ready 时，持久业务帧不能抢在 hasn.connected 前发送。"""
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()
    ws = FakeWS()
    rmod._ws_connections['node-x'] = ws
    rmod._ws_connection_ids['node-x'] = 'conn-x'
    redis.hash[rmod.NODE_GENERATION_KEY] = {'node-x': 'conn-x'}
    redis.lists[f'{busmod.PENDING_PREFIX}:node-x'] = ['BUSINESS']

    assert await busmod.WsDeliveryBus.drain_node('node-x') == 0
    assert ws.sent == []

    rmod._ws_ready_connection_ids['node-x'] = 'conn-x'
    assert await busmod.WsDeliveryBus.drain_node('node-x') == 1
    assert ws.sent == ['BUSINESS']

    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()


@pytest.mark.asyncio
async def test_deliver_local_malformed_is_noop() -> None:
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    rmod._ws_connections.clear()
    ws = FakeWS()
    rmod._ws_connections['node-a'] = ws

    await busmod.WsDeliveryBus._deliver_local({'payload': ''})  # 空 payload
    await busmod.WsDeliveryBus._deliver_local({'node_id': 'node-a'})  # 缺 payload
    await busmod.WsDeliveryBus._deliver_local({'node_id': '', 'payload': 'X'})  # 空 node_id
    assert ws.sent == []

    rmod._ws_connections.clear()


@pytest.mark.asyncio
async def test_publish_then_deliver_full_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """确定性模拟跨 worker：worker-A publish → worker-B（持有连接）_deliver_local 下发。"""
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()
    ws_x = FakeWS()
    rmod._ws_connections['node-x'] = ws_x  # 连接落在「本 worker」
    rmod._ws_connection_ids['node-x'] = 'conn-x'
    rmod._ws_ready_connection_ids['node-x'] = 'conn-x'
    redis.hash[rmod.NODE_GENERATION_KEY] = {'node-x': 'conn-x'}

    # 「另一个 worker」发布投递帧
    await busmod.WsDeliveryBus.publish_to_node('node-x', 'PAYLOAD')
    assert len(redis.published) == 1
    channel, message = redis.published[0]
    assert channel == busmod.WS_DELIVERY_CHANNEL

    # 「持有连接的 worker」订阅者收到该帧 → 下发
    await busmod.WsDeliveryBus._deliver_local(json.loads(message))
    assert ws_x.sent == ['PAYLOAD']

    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()


@pytest.mark.asyncio
async def test_publish_persists_payload_when_pubsub_wakeup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目标 worker 订阅短暂中断时，消息仍留在 node 待投队列等待周期重试。"""
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod

    redis = FakeRedis(fail_publish=True)
    monkeypatch.setattr(busmod, 'redis_client', redis)

    queued = await busmod.WsDeliveryBus.publish_to_node('node-x', 'PAYLOAD')

    assert queued is True
    assert redis.lists[f'{busmod.PENDING_PREFIX}:node-x'] == ['PAYLOAD']


@pytest.mark.asyncio
async def test_offline_messages_are_acked_only_as_original_queue_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补推期间并发追加的消息不能被旧领取确认误删。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(rmod, 'redis_client', redis)
    key = f'{rmod.OFFLINE_PREFIX}:h-owner'
    first = json.dumps({'created_time': '1', 'body': 'first'})
    second = json.dumps({'created_time': '2', 'body': 'second'})
    concurrent = json.dumps({'created_time': '3', 'body': 'concurrent'})
    redis.lists[key] = [first, second]

    router = rmod.NodeSessionService()
    messages, claims = await router.claim_offline_messages(['h-owner'])
    assert [message['body'] for message in messages] == ['first', 'second']

    redis.lists[key].append(concurrent)
    await router.ack_offline_messages(claims)
    assert redis.lists[key] == [concurrent]

    # 另一个并发消费者的迟到 ACK 前缀已不匹配，不能删除新消息。
    await router.ack_offline_messages(claims)
    assert redis.lists[key] == [concurrent]


@pytest.mark.asyncio
async def test_offline_messages_remain_unacked_when_ws_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_json 失败时不确认 Redis 离线队列。"""
    from backend.app.hasn_im.api import ws_node
    from backend.app.hasn_im.application.node_session_service import node_session_service

    claims = {'hasn:offline:h-owner': ['raw']}
    acked: list[dict[str, list[str]]] = []

    async def _claim(  # noqa: RUF029
        _entity_ids: list[str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        return [{'body': 'retry'}], claims

    async def _ack(value: dict[str, list[str]]) -> None:  # noqa: RUF029
        acked.append(value)

    monkeypatch.setattr(node_session_service, 'claim_offline_messages', _claim)
    monkeypatch.setattr(node_session_service, 'ack_offline_messages', _ack)

    with pytest.raises(RuntimeError, match='ws closed'):
        await ws_node._send_offline_messages(FakeWS(fail=True), ['h-owner'])
    assert acked == []


@pytest.mark.asyncio
async def test_drain_keeps_failed_send_for_current_connection_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发送失败不确认条目；同一当前代际恢复后可再次 drain 并成功下发。"""
    from backend.app.hasn_im.adapters.routing import delivery_bus as busmod
    from backend.app.hasn_im.adapters.routing import node_session_service as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()
    redis.hash[rmod.NODE_GENERATION_KEY] = {'node-x': 'conn-current'}
    redis.lists[f'{busmod.PENDING_PREFIX}:node-x'] = ['PAYLOAD']
    rmod._ws_connections['node-x'] = FakeWS(fail=True)
    rmod._ws_connection_ids['node-x'] = 'conn-current'
    rmod._ws_ready_connection_ids['node-x'] = 'conn-current'

    assert await busmod.WsDeliveryBus.drain_node('node-x') == 0
    assert redis.lists[f'{busmod.PROCESSING_PREFIX}:node-x'] == ['PAYLOAD']

    healthy = FakeWS()
    rmod._ws_connections['node-x'] = healthy
    assert await busmod.WsDeliveryBus.drain_node('node-x') == 1
    assert healthy.sent == ['PAYLOAD']
    assert redis.lists[f'{busmod.PENDING_PREFIX}:node-x'] == []
    assert redis.lists[f'{busmod.PROCESSING_PREFIX}:node-x'] == []

    rmod._ws_connections.clear()
    rmod._ws_connection_ids.clear()
    rmod._ws_ready_connection_ids.clear()


# ─────────────────────── ws_router 投递路由 ───────────────────────


@pytest.mark.asyncio
async def test_send_or_publish_local_direct_else_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    module._ws_connection_ids.clear()
    module._ws_ready_connection_ids.clear()
    published: list[tuple[str, str]] = []

    async def _spy(node_id: str, payload_json: str) -> bool:  # noqa: RUF029
        published.append((node_id, payload_json))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)

    ws_local = FakeWS()
    module._ws_connections['node-local'] = ws_local
    module._ws_connection_ids['node-local'] = 'conn-local'
    module._ws_ready_connection_ids['node-local'] = 'conn-local'
    redis.hash[module.NODE_GENERATION_KEY] = {'node-local': 'conn-local'}
    router = module.NodeSessionService()

    # 本 worker 持有 → 直发，不经总线
    await router._send_or_publish('node-local', 'L')
    assert ws_local.sent == ['L']
    assert published == []

    # 本 worker 不持有 → 经总线
    await router._send_or_publish('node-remote', 'R')
    assert published == [('node-remote', 'R')]

    module._ws_connections.clear()
    module._ws_connection_ids.clear()
    module._ws_ready_connection_ids.clear()


@pytest.mark.asyncio
async def test_push_to_human_online_via_bus_else_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()  # 没有任何连接落在本 worker
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> bool:  # noqa: RUF029
        published.append((nid, pj))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.NodeSessionService()

    # 在线（presence 有节点，但都不在本 worker）→ 经总线投递，**不**入离线
    redis.sets[f'{module.USER_NODES_PREFIX}:h_u'] = {'node-1', 'node-2'}
    ok = await router._push_to_human('h_u', 'MSG')
    assert ok is True
    assert {n for n, _ in published} == {'node-1', 'node-2'}
    assert f'{module.OFFLINE_PREFIX}:h_u' not in redis.lists  # 在线不入离线队列

    # 离线（presence 无节点）→ 入离线队列，不经总线
    published.clear()
    off = await router._push_to_human('h_off', 'MSG2')
    assert off is False
    assert published == []
    assert redis.lists[f'{module.OFFLINE_PREFIX}:h_off'] == ['MSG2']

    module._ws_connections.clear()


@pytest.mark.asyncio
async def test_push_to_entity_online_via_bus_else_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> bool:  # noqa: RUF029
        published.append((nid, pj))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.NodeSessionService()

    # Agent 在线（entity_node 有路由，连接不在本 worker）→ 经总线
    redis.hash[module.ENTITY_NODE_KEY] = {'a_x': 'node-7'}
    ok = await router._push_to_entity('a_x', 'TOOL')
    assert ok is True
    assert published == [('node-7', 'TOOL')]

    # Agent 离线（无路由）→ 入离线
    published.clear()
    off = await router._push_to_entity('a_off', 'TOOL2')
    assert off is False
    assert published == []
    assert redis.lists[f'{module.OFFLINE_PREFIX}:a_off'] == ['TOOL2']

    module._ws_connections.clear()


@pytest.mark.asyncio
async def test_route_falls_back_offline_when_durable_node_queue_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 presence 但 node 队列不可写时，不得谎报在线投递成功。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    redis.hash[module.ENTITY_NODE_KEY] = {'a_x': 'node-7'}

    async def _failed_publish(  # noqa: RUF029
        _node_id: str,
        _payload_json: str,
    ) -> bool:
        return False

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _failed_publish)

    delivered = await module.NodeSessionService().push_message_to('a_x', {'body': 'retry-me'})

    assert delivered is False
    assert redis.lists[f'{module.OFFLINE_PREFIX}:a_x']


@pytest.mark.asyncio
async def test_push_self_sync_excludes_sender_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """多端自同步：投给 owner 的其它节点，跳过发送节点，不入离线。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> bool:  # noqa: RUF029
        published.append((nid, pj))
        return True

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.NodeSessionService()

    redis.sets[f'{module.USER_NODES_PREFIX}:h_owner'] = {'node-send', 'node-other'}
    await router.push_self_sync('h_owner', {'method': 'hasn.message.received', 'params': {}}, 'node-send')

    # 只投给 node-other（跳过发送节点 node-send），且不入离线队列
    assert {n for n, _ in published} == {'node-other'}
    assert not any(k.startswith(module.OFFLINE_PREFIX) for k in redis.lists)

    module._ws_connections.clear()
