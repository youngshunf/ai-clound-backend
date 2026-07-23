"""回归：联系人「TA 的 AI 分身」必须带真实在线状态 + 正确描述。

在线状态来源：**Redis presence**（`ws_router.get_online_map`，叠加节点存活心跳
node_alive 门控），与 `sync_agents` 同源、断线即离线。**不再**读持久列
`HasnAgents.online_status`——该列由心跳写、断线不清零，agent 非优雅退出后会永远
停在 online（僵尸在线），P3 的 TTL 僵尸回收只对 Redis presence 生效。持久
`last_heartbeat_at` 仅作「最后已知时间」展示。
描述来源：HasnAgents.description（agent 角色介绍，bio 多为空）。

列表端点与详情构造共用 HasnContactsService.fetch_owned_agents_with_status，
保证集合 / 在线状态 / 描述三者一致。

本单测不依赖真实数据库：mock db.execute 截获 SELECT 并喂入假 agent 行，Redis
presence 用 FakeRedis 替身。断言（1）查询直接从 hasn_agents 过滤 social_enabled，
不 JOIN 运行时上报表；（2）在线状态取自 presence 而非持久列（presence 胜过过期列）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sqlalchemy.dialects.postgresql import dialect as pg_dialect

from backend.app.hasn.service.hasn_contacts_service import HasnContactsService


class FakeRedis:
    """最小 Redis 替身：presence 读取所需 hmget（路由表）+ exists（节点存活键）。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.strings: dict[str, Any] = {}

    async def hmget(self, key: str, fields: list[str]) -> list[Any]:
        bucket = self.hashes.get(key, {})
        return [bucket.get(f) for f in fields]

    async def exists(self, key: str) -> int:
        return 1 if key in self.strings else 0

    async def mget(self, keys: list[str]) -> list[Any]:
        # 就绪键批量取（get_online_map 用），缺失返回 None。
        return [self.strings.get(k) for k in keys]


def _fake_agent(suffix: str, online_status: str | None, heartbeat: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        hasn_id=f'a_{suffix}',
        star_id=f'star_{suffix}',
        display_name=f'分身{suffix}',
        agent_name=f'agent_{suffix}',
        avatar=None,
        type='desktop',
        role='specialist',
        profession=f'专家{suffix}',
        description=f'角色描述{suffix}',
        bio='',
        # 持久列故意设成与 presence 相反，证明在线判定取自 presence 而非此列。
        online_status=online_status,
        last_heartbeat_at=heartbeat,
    )


def _mock_db(agents: list) -> tuple[MagicMock, list]:
    """构造 db，使 db.execute(...).scalars().all() 返回 agents，并记录 statement。"""
    captured: list = []
    scalars = MagicMock()
    scalars.all.return_value = agents
    result = MagicMock()
    result.scalars.return_value = scalars

    async def fake_execute(stmt: object) -> MagicMock:
        captured.append(stmt)
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    return db, captured


@pytest.mark.asyncio
async def test_query_filters_hasn_agents_without_runtime_reports_join() -> None:
    """SELECT 应直接从 hasn_agents 过滤 social_enabled，不引用运行时上报表。"""
    db, captured = _mock_db(agents=[])

    await HasnContactsService.fetch_owned_agents_with_status(db, 'h_owner')

    assert len(captured) == 1, f'expected 1 select, got {len(captured)}'
    sql = str(captured[0].compile(dialect=pg_dialect())).lower()
    assert 'hasn_agents' in sql
    assert 'social_enabled' in sql
    assert 'deleted_at' in sql
    # 不再 JOIN 空置的运行时上报表。
    assert 'hasn_agent_runtime_reports' not in sql, f'unexpected runtime-report join: {sql}'


@pytest.mark.asyncio
async def test_online_status_comes_from_presence_not_stale_column(monkeypatch) -> None:
    """在线判定取自 Redis presence（node_alive 门控），胜过过期持久列；description/last_seen 带出。"""
    from backend.app.hasn_im.adapters.routing import node_session_service as ws_module

    redis = FakeRedis()
    monkeypatch.setattr(ws_module, 'redis_client', redis)
    # a_on 路由在 node_X 且 node_X 心跳存活 + runtime 就绪键在 → 在线（即便持久列写着 offline）。
    redis.hashes[ws_module.ENTITY_NODE_KEY] = {'a_on': 'node_X'}
    redis.strings[f'{ws_module.NODE_ALIVE_PREFIX}:node_X'] = '1'
    # 在线语义收紧：还需 agent 就绪键（心跳 online+ok 才写）才算真在线。
    redis.strings[f'{ws_module.AGENT_READY_PREFIX}:a_on'] = '1'
    # a_zombie 路由仍指向 node_dead，但该节点心跳已过期（无存活键）→ 离线（僵尸回收）。
    redis.hashes[ws_module.ENTITY_NODE_KEY]['a_zombie'] = 'node_dead'

    beat = datetime(2026, 6, 2, 10, 14, tzinfo=timezone.utc)
    agents = [
        _fake_agent('on', 'offline', beat),       # 持久列 offline，presence online
        _fake_agent('zombie', 'online', beat),    # 持久列 online（断线不清零），presence 离线
        _fake_agent('none', None, None),          # 无任何 presence
    ]
    db, _ = _mock_db(agents)

    out = await HasnContactsService.fetch_owned_agents_with_status(db, 'h_owner')

    assert [a['online_status'] for a in out] == ['online', 'offline', 'offline']
    assert out[0]['profession'] == '专家on'  # 专家名称带出，供联系人名下分身卡展示
    assert out[0]['description'] == '角色描述on'
    assert out[0]['last_seen_at'] == beat.isoformat()
    assert out[2]['last_seen_at'] is None
    assert out[0]['name'] == '分身on'
