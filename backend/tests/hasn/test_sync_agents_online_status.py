"""sync_agents 实时在线回填测试。

换设备登录时 daemon 据快照 online_status 判断「其他设备是否仍在线持有该 agent」：
仅当在线持有才跳过自动绑定，离线则接管到当前设备。因此 online_status 必须来自
Redis presence（断线即清），不能来自持久列（断线不清零）。本测试锁住这一行为。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class FakeRedis:
    """最小 Redis 替身：只实现 presence 读写所需的 hset/hmget。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}

    async def hset(self, key: str, field: str, value: Any) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hmget(self, key: str, fields: list[str]) -> list[Any]:
        bucket = self.hashes.get(key, {})
        return [bucket.get(field) for field in fields]


class FakeGateway:
    """注入到 HasnAgentProfileService 的 gateway 替身。"""

    def __init__(self, agents: list[Any]) -> None:
        self._agents = agents

    async def list_owner_agents(self, db: Any, *, owner_id: str, after_revision: Any) -> list[Any]:
        return list(self._agents)

    async def owns_owner(self, db: Any, *, owner_id: str, user_id: Any) -> bool:
        return True


def _fake_agent(hasn_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        hasn_id=hasn_id,
        star_id=f'star_{hasn_id}',
        owner_id='h_owner',
        agent_name=hasn_id,
        display_name=hasn_id,
        status='active',
        profile_revision=1,
        binding_status='bound',
        binding_node_id='node-A',
    )


@pytest.mark.asyncio
async def test_get_online_map_reflects_redis_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as ws_module

    redis = FakeRedis()
    monkeypatch.setattr(ws_module, 'redis_client', redis)
    await redis.hset(ws_module.ENTITY_NODE_KEY, 'a_online', 'node-A')

    router = ws_module.WsRouterService()
    result = await router.get_online_map(['a_online', 'a_offline'])

    assert result == {'a_online': True, 'a_offline': False}
    assert await router.get_online_map([]) == {}


@pytest.mark.asyncio
async def test_sync_agents_backfills_online_status_from_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.schema.hasn_agents import AgentSyncRequest
    from backend.app.hasn.service import hasn_agents_service as svc_module
    from backend.app.hasn.service import ws_router as ws_module

    redis = FakeRedis()
    monkeypatch.setattr(ws_module, 'redis_client', redis)
    # a_online 在 node-A 有实时 presence；a_offline 没有任何 presence（旧设备已离线）。
    await redis.hset(ws_module.ENTITY_NODE_KEY, 'a_online', 'node-A')

    async def _fake_common_skill_snapshot(db: Any) -> tuple[Any, str]:
        return ([], 'rev-1')

    monkeypatch.setattr(svc_module, 'get_common_skill_snapshot', _fake_common_skill_snapshot)

    service = svc_module.HasnAgentProfileService(
        gateway=FakeGateway([_fake_agent('a_online'), _fake_agent('a_offline')])
    )
    response = await service.sync_agents(
        object(),
        AgentSyncRequest(owner_id='h_owner', after_revision=None, include_disabled=True),
        user_id=1,
    )

    status = {snapshot.hasn_id: snapshot.online_status for snapshot in response.agents}
    # 即使两者持久绑定都指向 node-A，只有真正在线的 a_online 才回填为 online；
    # a_offline 回填为 offline → daemon 才能接管它到新设备。
    assert status == {'a_online': 'online', 'a_offline': 'offline'}
