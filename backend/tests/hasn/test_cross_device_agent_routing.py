"""多设备跨设备消息路由（Case B）的 ws_router 投递契约测试。

场景：主人福仔在「设备 A」给「绑定在设备 B」的自己 Agent 发消息。
本地 daemon（设备 A）本机不可达该 Agent，但因 Agent 在云端别处在线
（bound_remote），不再本地 422，而是改走云端 wire。云端 route_message
经铁律②放行（owner controls own agent，见 test_iron_laws.py）后，做两件事：

  1) `_push_to_entity(agent_id)` —— 把消息投到 Agent 当前所在节点（设备 B），
     设备 B 的 runtime 才能收到并回复；
  2) `push_to_owner_excluding_agent_node(owner_id, agent_id, owner_copy)` ——
     把同一条消息的 owner_copy 回灌主人其余在线设备（设备 A），但**排除**
     Agent 实体所在节点（设备 B），避免 B 同节点收两遍。

这正是「在其他设备登录也能和自己 Agent 正常多轮对话」依赖的云端投递缝。
本测试驱动真实 `NodeSessionService` 方法，仅在 Redis / WebSocket 基础设施边界
使用 in-process fake（与已有 test_ws_router_and_route_guard.py 同款约定）。

设计事实源：docs/产品与技术/技术设计/02-平台能力/消息与会话/07-跨设备消息路由（本地短路与跨设备选路）.md
"""

from __future__ import annotations

import json

from typing import Any

import pytest


class FakeRedis:
    """ws_router 用到的最小 Redis 子集（hash + set + list）。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[Any]] = {}
        self.lists: dict[str, list[Any]] = {}

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

    async def expire(self, key: str, ttl: int) -> None:
        return None


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, value: str) -> None:
        self.sent.append(value)

    def bodies(self) -> list[str]:
        return [json.loads(raw).get('body') for raw in self.sent]


def _build_router(monkeypatch):
    """装配真实 NodeSessionService + 双节点（设备 A / 设备 B）在线连接。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()

    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()
    module._ws_connections['node_A'] = ws_a
    module._ws_connections['node_B'] = ws_b

    router = module.NodeSessionService()
    return module, router, redis, ws_a, ws_b


@pytest.mark.asyncio
async def test_owner_message_to_remote_bound_agent_lands_on_agent_node(monkeypatch) -> None:
    """主人发给「绑在设备 B」的 Agent → 消息投到设备 B（runtime 所在），不投设备 A。"""
    module, router, redis, ws_a, ws_b = _build_router(monkeypatch)

    owner_id = 'h_fuzai'
    agent_id = 'a_fuzai_brain'
    # 主人两台设备都在线；Agent 实体绑在设备 B。
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', 'node_A')
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', 'node_B')
    await redis.hset(module.ENTITY_NODE_KEY, agent_id, 'node_B')

    delivered = await router.push_message_to(agent_id, {'created_time': '1', 'body': '你好分身'})

    assert delivered is True
    assert ws_b.bodies() == ['你好分身'], 'Agent 所在的设备 B 必须收到主人的消息'
    assert ws_a.bodies() == [], '设备 A 不是 Agent 实体节点，实体投递不应落到 A'


@pytest.mark.asyncio
async def test_owner_copy_fans_to_other_device_excluding_agent_node(monkeypatch) -> None:
    """owner_copy 回灌主人「其余设备」（设备 A），排除 Agent 所在节点（设备 B）。"""
    module, router, redis, ws_a, ws_b = _build_router(monkeypatch)

    owner_id = 'h_fuzai'
    agent_id = 'a_fuzai_brain'
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', 'node_A')
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', 'node_B')
    await redis.hset(module.ENTITY_NODE_KEY, agent_id, 'node_B')

    pushed = await router.push_to_owner_excluding_agent_node(
        owner_id, agent_id, {'created_time': '2', 'body': 'owner_copy:你好分身'}
    )

    assert pushed is True
    assert ws_a.bodies() == ['owner_copy:你好分身'], '发起设备 A 必须收到 owner_copy'
    assert ws_b.bodies() == [], 'Agent 节点 B 已由实体投递收过，owner_copy 必须排除 B，避免双份'


@pytest.mark.asyncio
async def test_single_device_no_double_delivery_when_agent_on_owner_node(monkeypatch) -> None:
    """单设备：Agent 跑在主人唯一节点上 → owner_copy 排除该节点 → 主人不收第二份。

    这是「发一条收两条回复」回归的护栏：同 daemon 同时持有 owner 与 agent 实体时，
    实体投递已送达，owner fanout 必须把该节点排除干净。
    """
    module, router, redis, ws_a, ws_b = _build_router(monkeypatch)

    owner_id = 'h_solo'
    agent_id = 'a_solo_brain'
    # 只有设备 B 在线，Agent 也在设备 B（与主人同节点）。
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', 'node_B')
    await redis.hset(module.ENTITY_NODE_KEY, agent_id, 'node_B')

    # 实体投递：设备 B 收到一次。
    await router.push_message_to(agent_id, {'created_time': '1', 'body': 'm'})
    # owner fanout：排除 Agent 节点 B → 无其余设备 → 主人不再额外收 owner_copy。
    pushed = await router.push_to_owner_excluding_agent_node(
        owner_id, agent_id, {'created_time': '2', 'body': 'owner_copy:m'}
    )

    assert pushed is False, '唯一在线节点即 Agent 节点，被排除后无可投递目标'
    assert ws_b.bodies() == ['m'], '设备 B 仅经实体投递收到一次，没有 owner_copy 双份'
    assert ws_a.bodies() == []
