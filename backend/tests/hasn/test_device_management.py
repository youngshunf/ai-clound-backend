"""P2 设备管理：GeoLite2 归属地降级 + ws_router 设备 presence/远程登出 + 节点元数据回填。

覆盖（零 Mock 业务逻辑；Redis/WS 用基础设施 fake，PG 用真实库末尾回滚）：
- geoip_service：私网/回环/非法 IP、以及无 mmdb 的公网 IP → 一律 None（绝不伪造城市）。
- ws_router：is_node_online / get_entity_node / disconnect_node（清 presence + 释放名下 Agent，
  使其在 ENTITY_NODE_KEY 上离线 → 他机可接管）。
- hasn_nodes_service.update_runtime_metadata：回填 IP/归属地/平台/版本；ip_location 跟随 IP，
  未知时如实写 None；ip_address 为空时不覆盖既有元数据。

真实 PG 部分需 export DATABASE_PORT=15432（指向本地开发 PG）。
设计事实源：docs/hasn-node设计文档/多设备登录与跨设备消息路由/00-设计总览.md §4
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import geoip_service
from backend.database.db import SQLALCHEMY_DATABASE_URL


# ─────────────────────────── geoip 降级（无外部依赖） ───────────────────────────

@pytest.mark.parametrize(
    'ip',
    ['192.168.1.10', '10.0.0.1', '127.0.0.1', '::1', 'not-an-ip', '', None],
)
def test_geoip_returns_none_for_private_or_invalid(ip):
    """私网/回环/非法/空 IP → None（永不伪造归属地）。"""
    assert geoip_service.lookup_location(ip) is None


def test_geoip_public_ip_without_mmdb_is_none():
    """公网 IP 但 mmdb 缺失（部署前提未满足）→ None，如实「未知归属地」。"""
    # 测试环境无 GeoLite2-City.mmdb；零 Mock 要求此处必须留空而非编造城市。
    assert geoip_service.lookup_location('8.8.8.8') is None


# ─────────────────────────── ws_router 设备 presence / 远程登出 ───────────────────────────

class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        self.strings: dict[str, Any] = {}

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

    async def delete(self, key: str) -> None:
        self.hashes.pop(key, None)
        self.sets.pop(key, None)
        self.lists.pop(key, None)
        self.strings.pop(key, None)


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed_with: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = '') -> None:
        self.closed_with = (code, reason)


@pytest.mark.asyncio
async def test_node_online_and_entity_node_lookup(monkeypatch):
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.WsRouterService()

    # P3：在线 = 存活心跳键在（非 NODE_CONN_KEY 残留）。
    assert await router.is_node_online('node_X') is False
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:node_X', '1', ex=90)
    assert await router.is_node_online('node_X') is True

    assert await router.get_entity_node('a_agent') is None
    await redis.hset(module.ENTITY_NODE_KEY, 'a_agent', 'node_X')
    assert await router.get_entity_node('a_agent') == 'node_X'


@pytest.mark.asyncio
async def test_disconnect_node_clears_presence_and_releases_agents(monkeypatch):
    """远程登出核心契约：断开节点 → 清节点 presence + 名下 Agent 在路由表离线（他机可接管）+ 关 WS。"""
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.WsRouterService()

    node_id = 'node_B'
    owner_id = 'h_owner'
    agent_id = 'a_brain'
    ws = FakeWebSocket()
    module._ws_connections[node_id] = ws

    # 模拟节点在线：conn 记录 + 存活心跳键 + 节点实体集合 + 路由表 + owner 节点集合
    await redis.hset(module.NODE_CONN_KEY, node_id, '{}')
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:{node_id}', '1', ex=90)
    await redis.sadd(f'{module.NODE_ENTITIES_PREFIX}:{node_id}', owner_id)
    await redis.sadd(f'{module.NODE_ENTITIES_PREFIX}:{node_id}', agent_id)
    await redis.hset(module.ENTITY_NODE_KEY, owner_id, node_id)
    await redis.hset(module.ENTITY_NODE_KEY, agent_id, node_id)
    await redis.sadd(f'{module.USER_NODES_PREFIX}:{owner_id}', node_id)

    # 断开前：节点在线、名下 agent 在线（其节点心跳在）
    assert await router.is_node_online(node_id) is True
    assert await router.is_agent_online(agent_id) is True

    in_process = await router.disconnect_node(node_id)

    assert in_process is True
    assert ws.closed_with == (4002, 'remote logout'), 'WS 必须被主动关闭'
    assert await router.is_node_online(node_id) is False, '节点 conn presence 必须清除'
    # 名下 Agent 在路由表离线 → get_online_map/is_agent_online 判离线 → 他机可接管
    assert await router.get_entity_node(agent_id) is None
    assert await router.is_agent_online(agent_id) is False
    # owner 在该节点的 user_nodes 也清除
    assert node_id not in await redis.smembers(f'{module.USER_NODES_PREFIX}:{owner_id}')


@pytest.mark.asyncio
async def test_disconnect_node_not_in_process_still_clears_presence(monkeypatch):
    """连接落在其它 worker（本进程无 ws）→ 返回 False，但共享 presence 仍清干净。"""
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()
    router = module.WsRouterService()

    node_id = 'node_remote'
    await redis.hset(module.NODE_CONN_KEY, node_id, '{}')
    await redis.set(f'{module.NODE_ALIVE_PREFIX}:{node_id}', '1', ex=90)
    await redis.sadd(f'{module.NODE_ENTITIES_PREFIX}:{node_id}', 'a_x')
    await redis.hset(module.ENTITY_NODE_KEY, 'a_x', node_id)

    assert await router.is_node_online(node_id) is True

    in_process = await router.disconnect_node(node_id)

    assert in_process is False
    assert await router.is_node_online(node_id) is False
    assert await router.get_entity_node('a_x') is None


# ─────────────────────────── 节点元数据回填（真实 PG） ───────────────────────────

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_runtime_metadata_writes_and_respects_zero_fake(db_session):
    from backend.app.hasn.model.hasn_nodes import HasnNodes
    from backend.app.hasn.service.hasn_nodes_service import hasn_nodes_service

    node_id = f'n_test_{uuid.uuid4().hex[:10]}'
    db_session.add(HasnNodes(
        node_id=node_id,
        node_type='desktop',
        node_info={},
        capacity=3,
        status='active',
    ))
    await db_session.flush()

    # 1) 正常回填：IP + 归属地 + 平台 + 版本
    await hasn_nodes_service.update_runtime_metadata(
        db=db_session, node_id=node_id,
        ip_address='203.0.113.7', ip_location='上海, 上海市, 中国',
        device_platform='macos', app_version='0.3.1',
    )
    row = (await db_session.execute(
        select(HasnNodes).where(HasnNodes.node_id == node_id)
    )).scalar_one()
    assert row.ip_address == '203.0.113.7'
    assert row.ip_location == '上海, 上海市, 中国'
    assert row.device_platform == 'macos'
    assert row.app_version == '0.3.1'

    # 2) 零 Mock：拿到 IP 但归属地未知 → ip_location 如实写 None，不沿用旧值、不伪造
    await hasn_nodes_service.update_runtime_metadata(
        db=db_session, node_id=node_id,
        ip_address='198.51.100.9', ip_location=None,
    )
    row = (await db_session.execute(
        select(HasnNodes).where(HasnNodes.node_id == node_id)
    )).scalar_one()
    assert row.ip_address == '198.51.100.9'
    assert row.ip_location is None

    # 3) ip_address 为空 → 不覆盖既有元数据（platform/version 保留）
    await hasn_nodes_service.update_runtime_metadata(
        db=db_session, node_id=node_id,
        ip_address=None, device_platform=None, app_version=None,
    )
    row = (await db_session.execute(
        select(HasnNodes).where(HasnNodes.node_id == node_id)
    )).scalar_one()
    assert row.ip_address == '198.51.100.9'
    assert row.device_platform == 'macos'
    assert row.app_version == '0.3.1'
