"""方案 B：Redis offline 影子与 PostgreSQL sync 事实对账。"""

from __future__ import annotations

import json
import logging

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import HasnAgents
from backend.app.hasn_im.adapters.routing.offline_frame_policy import (
    OfflineFrameCategory,
    OfflineFrameDecision,
    OfflineFramePolicyError,
    classify_offline_frame,
)
from backend.app.hasn_im.adapters.routing.redis_presence_store import (
    OFFLINE_PREFIX,
    OFFLINE_TTL,
)
from backend.app.hasn_im.observability.metrics import HASN_OFFLINE_SHADOW_MESSAGES
from backend.database.redis import redis_client
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')
_METHOD_TO_SYNC_EVENT = MappingProxyType({
    'hasn.message.new': 'message.new',
    'hasn.message.invalidated': 'message.recalled',
    'hasn.conversation.invalidated': 'conversation.updated',
    'hasn.task.exec': 'task.exec',
})


@dataclass(frozen=True, slots=True)
class ShadowIdentity:
    """一条 owner 维度的稳定对账身份。"""

    owner_id: str
    event_type: str
    identity: str


@dataclass(frozen=True, slots=True)
class OfflineShadowReport:
    """一次七天窗口对账的低基数统计结果。"""

    redis_candidates: int
    sync_candidates: int
    both: int
    redis_only: int
    sync_only: int
    snapshot_backed: int
    malformed: int

    @property
    def redis_only_unrecoverable(self) -> int:
        """缺少直接 sync event 且不能由权威快照解释的 Redis 独有数量。"""
        return self.redis_only


def compare_shadow_identities(
    *,
    redis_identities: set[ShadowIdentity],
    sync_identities: set[ShadowIdentity],
    snapshot_backed: int,
    malformed: int,
) -> OfflineShadowReport:
    """比较两个稳定身份集合，不在返回值或指标中暴露具体 owner/事件 ID。"""
    return OfflineShadowReport(
        redis_candidates=len(redis_identities),
        sync_candidates=len(sync_identities),
        both=len(redis_identities & sync_identities),
        redis_only=len(redis_identities - sync_identities),
        sync_only=len(sync_identities - redis_identities),
        snapshot_backed=snapshot_backed,
        malformed=malformed,
    )


def merge_recent_and_historical_matches(
    *,
    redis_identities: set[ShadowIdentity],
    recent_sync_identities: set[ShadowIdentity],
    historical_sync_identities: set[ShadowIdentity],
) -> set[ShadowIdentity]:
    """只用历史 sync 修复当前 Redis 候选，避免扩大七天 sync-only 统计。"""
    return recent_sync_identities | (historical_sync_identities & redis_identities)


def shadow_event_type(decision: OfflineFrameDecision) -> str | None:
    """把帧映射到 sync 类型；未补齐的 gap 使用显式不可恢复命名空间。"""
    event_type = _METHOD_TO_SYNC_EVENT.get(decision.method)
    if event_type is not None:
        return event_type
    if decision.category is OfflineFrameCategory.GAP:
        return f'gap:{decision.method}'
    return None


def _text(value: str | bytes) -> str:
    return value.decode('utf-8') if isinstance(value, bytes) else value


async def _offline_keys(offline_keys: Sequence[str] | None) -> list[str]:
    if offline_keys is not None:
        return list(dict.fromkeys(offline_keys))
    keys: list[str] = [_text(key) async for key in redis_client.scan_iter(match=f'{OFFLINE_PREFIX}:*', count=500)]
    return keys


async def _resolve_target_owners(
    db: AsyncSession,
    target_ids: set[str],
) -> dict[str, str]:
    agent_ids = sorted(target for target in target_ids if target.startswith('a_'))
    owners = {target: target for target in target_ids if not target.startswith('a_')}
    if agent_ids:
        rows = (
            await db.execute(select(HasnAgents.hasn_id, HasnAgents.owner_id).where(HasnAgents.hasn_id.in_(agent_ids)))
        ).all()
        owners.update({str(agent_id): str(owner_id) for agent_id, owner_id in rows if owner_id})
    return owners


async def _load_sync_identities(
    db: AsyncSession,
    *,
    owners: set[str],
    since: datetime,
) -> set[ShadowIdentity]:
    if not owners:
        return set()
    statement = sa.text(
        f'SELECT owner_id, event_type, aggregate_id, source_event_id '
        f'FROM {_EVENTS} '
        f'WHERE occurred_at >= :since '
        f'AND owner_id IN :owners '
        "AND event_type IN ('message.new', 'message.recalled', "
        "'conversation.updated', 'task.exec')"
    ).bindparams(sa.bindparam('owners', expanding=True))
    rows = (
        await db.execute(
            statement,
            {
                'since': since,
                'owners': sorted(owners),
            },
        )
    ).mappings()
    identities: set[ShadowIdentity] = set()
    for row in rows:
        event_type = str(row['event_type'])
        raw_identity = row['aggregate_id'] if event_type == 'message.new' else row['source_event_id']
        if raw_identity:
            identities.add(
                ShadowIdentity(
                    owner_id=str(row['owner_id']),
                    event_type=event_type,
                    identity=str(raw_identity),
                )
            )
    return identities


async def _load_historical_redis_matches(
    db: AsyncSession,
    *,
    redis_identities: set[ShadowIdentity],
) -> set[ShadowIdentity]:
    """精确查询当前 Redis 候选在 PostgreSQL 中仍保留的历史 sync 事实。"""
    matches: set[ShadowIdentity] = set()
    by_event_type: dict[str, set[ShadowIdentity]] = {}
    for identity in redis_identities:
        by_event_type.setdefault(identity.event_type, set()).add(identity)

    for event_type, candidates in by_event_type.items():
        identity_column = 'aggregate_id' if event_type == 'message.new' else 'source_event_id'
        statement = sa.text(
            f'SELECT owner_id, event_type, aggregate_id, source_event_id '
            f'FROM {_EVENTS} '
            'WHERE owner_id IN :owners '
            'AND event_type = :event_type '
            f'AND {identity_column} IN :identities'
        ).bindparams(
            sa.bindparam('owners', expanding=True),
            sa.bindparam('identities', expanding=True),
        )
        rows = (
            await db.execute(
                statement,
                {
                    'owners': sorted({candidate.owner_id for candidate in candidates}),
                    'event_type': event_type,
                    'identities': sorted({candidate.identity for candidate in candidates}),
                },
            )
        ).mappings()
        for row in rows:
            row_event_type = str(row['event_type'])
            raw_identity = row['aggregate_id'] if row_event_type == 'message.new' else row['source_event_id']
            if raw_identity:
                matches.add(
                    ShadowIdentity(
                        owner_id=str(row['owner_id']),
                        event_type=row_event_type,
                        identity=str(raw_identity),
                    )
                )
    return matches & redis_identities


def _publish_report(report: OfflineShadowReport) -> None:
    values = {
        'both': report.both,
        'redis_only': report.redis_only,
        'sync_only': report.sync_only,
        'snapshot_backed': report.snapshot_backed,
        'malformed': report.malformed,
        'redis_only_unrecoverable': report.redis_only_unrecoverable,
    }
    for result, value in values.items():
        HASN_OFFLINE_SHADOW_MESSAGES.labels(result=result).set(value)


async def collect_offline_shadow_report(
    db: AsyncSession,
    *,
    offline_keys: Sequence[str] | None = None,
    now: datetime | None = None,
    publish_metrics: bool = True,
) -> OfflineShadowReport:
    """核对七天 Redis 影子与同 owner 的 PostgreSQL sync event。

    直接 sync 覆盖的帧进入三集合对账；联系人等权威快照恢复帧单列
    `snapshot_backed`，不能误报为 `redis_only_unrecoverable`。Redis 只保存现有离线帧，
    本函数不写用户 ID 标签，也不删除任何影子证据。
    """
    current = now or timezone.now()
    since = current - timedelta(seconds=OFFLINE_TTL)
    parsed: list[tuple[str, str, str]] = []
    snapshot_backed = 0
    malformed = 0
    for key in await _offline_keys(offline_keys):
        target_id = key.removeprefix(f'{OFFLINE_PREFIX}:')
        if not target_id or target_id == key:
            malformed += 1
            continue
        for raw in await redis_client.lrange(key, 0, -1):
            try:
                decision = classify_offline_frame(_text(raw))
            except (OfflineFramePolicyError, UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
                continue
            if decision.category is OfflineFrameCategory.TRANSIENT:
                malformed += 1
                continue
            event_type = shadow_event_type(decision)
            if event_type is None:
                snapshot_backed += 1
                continue
            if decision.identity is None:
                malformed += 1
                continue
            parsed.append((target_id, event_type, decision.identity))

    target_owners = await _resolve_target_owners(
        db,
        {target_id for target_id, _, _ in parsed},
    )
    redis_identities = {
        ShadowIdentity(
            owner_id=target_owners.get(target_id, target_id),
            event_type=event_type,
            identity=identity,
        )
        for target_id, event_type, identity in parsed
    }
    recent_sync_identities = await _load_sync_identities(
        db,
        owners={identity.owner_id for identity in redis_identities},
        since=since,
    )
    historical_sync_identities = await _load_historical_redis_matches(
        db,
        redis_identities=redis_identities,
    )
    sync_identities = merge_recent_and_historical_matches(
        redis_identities=redis_identities,
        recent_sync_identities=recent_sync_identities,
        historical_sync_identities=historical_sync_identities,
    )
    report = compare_shadow_identities(
        redis_identities=redis_identities,
        sync_identities=sync_identities,
        snapshot_backed=snapshot_backed,
        malformed=malformed,
    )
    if publish_metrics:
        _publish_report(report)
    if report.redis_only_unrecoverable:
        logger.warning(
            '离线影子对账发现待自愈差异: redis_only_unrecoverable=%s',
            report.redis_only_unrecoverable,
        )
    return report


__all__ = [
    'OfflineShadowReport',
    'ShadowIdentity',
    'collect_offline_shadow_report',
    'compare_shadow_identities',
    'merge_recent_and_historical_matches',
    'shadow_event_type',
]
