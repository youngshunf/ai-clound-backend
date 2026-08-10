"""P3 presence 心跳 + 僵尸回收：节点存活键 TTL 门控（真实 NodeSessionService，FakeRedis）。

证明（不依赖 wall-clock TTL 过期；用「删 alive 键」模拟过期）：
- 注册写存活键 → 节点在线；删存活键（模拟 SIGKILL 后 TTL 过期，无心跳续期）→ 节点离线。
- `refresh_node_presence`（hasn.ping 触发）重写存活键 → 恢复在线。
- **僵尸回收核心**：ENTITY_NODE_KEY 仍指向某节点，但该节点存活键过期 → `is_agent_online`/
  `get_online_map` 判该 agent 离线（僵尸路由不再阻塞他机接管）。
- `unregister_node`（优雅退出 / 远程登出）删存活键 → 立即离线。

设计事实源：docs/产品与技术/技术设计/02-平台能力/身份与权限/设备与Presence/02-Presence心跳与在线三层判定.md §1。
"""

from __future__ import annotations

import builtins

from typing import Any

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.strings: dict[str, Any] = {}

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.strings[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self.strings else 0

    async def mget(self, keys: list[str]) -> list[Any]:
        # 就绪键批量取（get_online_map 用），缺失返回 None。
        return [self.strings.get(k) for k in keys]

    async def hset(self, key: str, field: str, value: Any) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key: str, field: str) -> Any:
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key: str, field: str) -> None:
        self.hashes.get(key, {}).pop(field, None)

    async def hmget(self, key: str, fields: list[str]) -> list[Any]:
        store = self.hashes.get(key, {})
        return [store.get(f) for f in fields]

    async def sadd(self, key: str, value: Any) -> None:
        self.sets.setdefault(key, builtins.set()).add(value)

    async def srem(self, key: str, value: Any) -> None:
        self.sets.get(key, builtins.set()).discard(value)

    async def smembers(self, key: str) -> builtins.set[Any]:
        return builtins.set(self.sets.get(key, builtins.set()))

    async def delete(self, key: str) -> None:
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
            for hasn_id in list(self.sets.get(entities_key, builtins.set())):
                if self.hashes.get(entity_node_key, {}).get(hasn_id) == node_id:
                    self.hashes.get(entity_node_key, {}).pop(hasn_id, None)
                if str(hasn_id).startswith('h_'):
                    self.sets.get(f'{user_nodes_prefix}:{hasn_id}', builtins.set()).discard(node_id)
            await self.delete(entities_key)
            self.hashes.get(node_conn_key, {}).pop(node_id, None)
            await self.delete(alive_key)
            self.hashes.get(generation_key, {}).pop(node_id, None)
            return 1
        raise AssertionError(f'unexpected eval numkeys={numkeys}')


class _DummyWs:
    pass


def _router(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, FakeRedis]:
    from backend.app.hasn_im.adapters.routing import node_session_service as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    return module, module.NodeSessionService(), redis


@pytest.mark.asyncio
async def test_register_sets_alive_then_expiry_then_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, router, redis = _router(monkeypatch)

    connection_id = await router.register_node('node_1', 'desktop', _DummyWs(), capacity=2)
    assert await router.is_node_online('node_1') is True
    # 存活键确由 register 写入（带 TTL，这里只验存在）
    assert f'{module.NODE_ALIVE_PREFIX}:node_1' in redis.strings

    # 模拟 SIGKILL 后 TTL 过期（无 hasn.ping 续期）：键消失 → 离线
    await redis.delete(f'{module.NODE_ALIVE_PREFIX}:node_1')
    assert await router.is_node_online('node_1') is False

    # hasn.ping 续期 → 恢复在线
    await router.refresh_node_presence('node_1', connection_id)
    assert await router.is_node_online('node_1') is True


@pytest.mark.asyncio
async def test_zombie_node_route_makes_agent_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENTITY_NODE_KEY 仍指向节点，但节点存活键过期 → agent 判离线（解除接管阻塞）。"""
    module, router, redis = _router(monkeypatch)

    agent_id = 'a_brain'
    node_id = 'node_zombie'
    await redis.hset(module.ENTITY_NODE_KEY, agent_id, node_id)
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:{node_id}', '1', ex=90)
    # 在线语义收紧：还需 runtime 就绪键（心跳 online+ok 才写），否则判「连接中/离线」。
    await redis.set(f'{module.AGENT_READY_PREFIX}:{agent_id}', '1', ex=90)

    # 心跳在 + runtime 就绪 → 在线
    assert await router.is_agent_online(agent_id) is True
    assert await router.get_online_map([agent_id]) == {agent_id: True}

    # 心跳过期（僵尸节点：路由表残留但节点已死）→ 离线
    await redis.delete(f'{module.NODE_ALIVE_PREFIX}:{node_id}')
    assert await router.is_agent_online(agent_id) is False
    assert await router.get_online_map([agent_id]) == {agent_id: False}
    # 路由表仍指向该节点（残留），但不再判在线
    assert await router.get_entity_node(agent_id) == node_id


@pytest.mark.asyncio
async def test_unregister_deletes_alive_key(monkeypatch: pytest.MonkeyPatch) -> None:
    module, router, redis = _router(monkeypatch)

    connection_id = await router.register_node('node_g', 'desktop', _DummyWs(), capacity=1)
    assert await router.is_node_online('node_g') is True

    await router.unregister_node('node_g', connection_id)
    assert await router.is_node_online('node_g') is False
    assert f'{module.NODE_ALIVE_PREFIX}:node_g' not in redis.strings
