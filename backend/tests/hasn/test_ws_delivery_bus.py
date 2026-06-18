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


class FakeWS:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def send_text(self, value: str) -> None:
        if self.fail:
            raise RuntimeError('ws closed')
        self.sent.append(value)


class FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, set[Any]] = {}
        self.hash: dict[str, dict[str, Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.published: list[tuple[str, str]] = []

    async def smembers(self, key: str) -> set[Any]:
        return set(self.sets.get(key, set()))

    async def hget(self, key: str, field: str) -> Any:
        return self.hash.get(key, {}).get(field)

    async def rpush(self, key: str, value: Any) -> None:
        self.lists.setdefault(key, []).append(value)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


# ─────────────────────── 投递总线 _deliver_local 路由 ───────────────────────


@pytest.mark.asyncio
async def test_deliver_local_only_to_held_connection() -> None:
    """本 worker 持有该 node → 下发；不持有 → 无操作（不误投其它本地连接）。"""
    from backend.app.hasn.service import ws_delivery_bus as busmod
    from backend.app.hasn.service import ws_router as rmod

    rmod._ws_connections.clear()
    ws_held = FakeWS()
    ws_other = FakeWS()
    rmod._ws_connections['node-held'] = ws_held
    rmod._ws_connections['node-other'] = ws_other

    # 投给本 worker 持有的 node-held → 仅它收到
    await busmod.WsDeliveryBus._deliver_local({'node_id': 'node-held', 'payload': 'P1'})
    assert ws_held.sent == ['P1']
    assert ws_other.sent == []

    # 投给本 worker 不持有的 node-remote → 本 worker 无人下发（交给持有它的 worker）
    await busmod.WsDeliveryBus._deliver_local({'node_id': 'node-remote', 'payload': 'P2'})
    assert ws_held.sent == ['P1']
    assert ws_other.sent == []

    rmod._ws_connections.clear()


@pytest.mark.asyncio
async def test_deliver_local_broadcast_hits_all_local() -> None:
    from backend.app.hasn.service import ws_delivery_bus as busmod
    from backend.app.hasn.service import ws_router as rmod

    rmod._ws_connections.clear()
    ws_a, ws_b = FakeWS(), FakeWS()
    rmod._ws_connections['node-a'] = ws_a
    rmod._ws_connections['node-b'] = ws_b

    await busmod.WsDeliveryBus._deliver_local({'broadcast': True, 'payload': 'B'})
    assert ws_a.sent == ['B']
    assert ws_b.sent == ['B']

    rmod._ws_connections.clear()


@pytest.mark.asyncio
async def test_deliver_local_malformed_is_noop() -> None:
    from backend.app.hasn.service import ws_delivery_bus as busmod
    from backend.app.hasn.service import ws_router as rmod

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
    from backend.app.hasn.service import ws_delivery_bus as busmod
    from backend.app.hasn.service import ws_router as rmod

    redis = FakeRedis()
    monkeypatch.setattr(busmod, 'redis_client', redis)
    rmod._ws_connections.clear()
    ws_x = FakeWS()
    rmod._ws_connections['node-x'] = ws_x  # 连接落在「本 worker」

    # 「另一个 worker」发布投递帧
    await busmod.WsDeliveryBus.publish_to_node('node-x', 'PAYLOAD')
    assert len(redis.published) == 1
    channel, message = redis.published[0]
    assert channel == busmod.WS_DELIVERY_CHANNEL

    # 「持有连接的 worker」订阅者收到该帧 → 下发
    await busmod.WsDeliveryBus._deliver_local(json.loads(message))
    assert ws_x.sent == ['PAYLOAD']

    rmod._ws_connections.clear()


# ─────────────────────── ws_router 投递路由 ───────────────────────


@pytest.mark.asyncio
async def test_send_or_publish_local_direct_else_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as module

    module._ws_connections.clear()
    published: list[tuple[str, str]] = []

    async def _spy(node_id: str, payload_json: str) -> None:
        published.append((node_id, payload_json))

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)

    ws_local = FakeWS()
    module._ws_connections['node-local'] = ws_local
    router = module.WsRouterService()

    # 本 worker 持有 → 直发，不经总线
    await router._send_or_publish('node-local', 'L')
    assert ws_local.sent == ['L']
    assert published == []

    # 本 worker 不持有 → 经总线
    await router._send_or_publish('node-remote', 'R')
    assert published == [('node-remote', 'R')]

    module._ws_connections.clear()


@pytest.mark.asyncio
async def test_push_to_human_online_via_bus_else_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()  # 没有任何连接落在本 worker
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> None:
        published.append((nid, pj))

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.WsRouterService()

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
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> None:
        published.append((nid, pj))

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.WsRouterService()

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
async def test_push_self_sync_excludes_sender_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """多端自同步：投给 owner 的其它节点，跳过发送节点，不入离线。"""
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    published: list[tuple[str, str]] = []

    async def _spy(nid: str, pj: str) -> None:
        published.append((nid, pj))

    monkeypatch.setattr(module.ws_delivery_bus, 'publish_to_node', _spy)
    router = module.WsRouterService()

    redis.sets[f'{module.USER_NODES_PREFIX}:h_owner'] = {'node-send', 'node-other'}
    await router.push_self_sync('h_owner', {'method': 'hasn.message.received', 'params': {}}, 'node-send')

    # 只投给 node-other（跳过发送节点 node-send），且不入离线队列
    assert {n for n, _ in published} == {'node-other'}
    assert not any(k.startswith(module.OFFLINE_PREFIX) for k in redis.lists)

    module._ws_connections.clear()
