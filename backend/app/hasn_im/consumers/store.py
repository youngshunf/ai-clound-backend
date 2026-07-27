"""hasn_im.consumers.store · 消费者位点/失败态/租约/事件抓取存储层（raw SQL·§7.2）

集中所有对 ``hasn_im_event_consumer_offsets`` / ``hasn_im_event_consumer_failures`` /
``hasn_im_integration_events`` 的读写；框架（framework.py）只调本层，不散写 SQL。

所有 DB 时间用 PG ``now()``，避免应用与 DB 时钟偏差影响 lease/退避判定。
R2 期物理表落 public（前缀 hasn_im_），R2-11 迁 hasn_im schema。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_im.consumers.base import IntegrationEvent
from backend.database.schema_names import SCHEMA_NAMES

_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OFFSETS = SCHEMA_NAMES.im_event_table('event_consumer_offsets')
_FAILURES = SCHEMA_NAMES.im_event_table('event_consumer_failures')


@dataclass(frozen=True)
class FailureState:
    """某消费者对某事件的失败态（值对象）。"""

    consumer_name: str
    event_seq: int
    attempts: int
    dead_lettered: bool
    resolution: str | None
    # next_attempt_at 是否已到（DB 侧比较 now()，避免时钟偏差）——None=无待重试
    retry_due: bool


async def try_acquire_lease(
    db: AsyncSession, *, consumer_name: str, owner: str, ttl_seconds: int
) -> bool:
    """尝试获取/续租消费者租约（同 consumer_name 同时刻单实例·§7.2）。

    仅当行不存在、或租约已过期、或本实例持有时，才写入 lease_owner + lease_until = now()+ttl。
    返回是否持有租约。
    """
    row = (
        await db.execute(
            sa.text(
                f'INSERT INTO {_OFFSETS} '  # noqa: S608 常量表名
                '(consumer_name, last_acked_seq, lease_owner, lease_until, updated_at) '
                # asyncpg 严格类型：int 参数不能拼进字符串再 ::interval，用 int * interval 保持整型
                "VALUES (:name, 0, :owner, now() + (:ttl * interval '1 second'), now()) "
                'ON CONFLICT (consumer_name) DO UPDATE '
                "SET lease_owner = :owner, lease_until = now() + (:ttl * interval '1 second'), "
                '    updated_at = now() '
                f'WHERE {_OFFSETS}.lease_until IS NULL '
                f'   OR {_OFFSETS}.lease_until < now() '
                f'   OR {_OFFSETS}.lease_owner = :owner '
                'RETURNING consumer_name'
            ),
            {'name': consumer_name, 'owner': owner, 'ttl': ttl_seconds},
        )
    ).scalar_one_or_none()
    return row is not None


async def release_lease(
    db: AsyncSession,
    *,
    consumer_name: str,
    owner: str,
) -> None:
    """仅由当前持有者释放租约，供 worker 优雅退出后立即接管。"""
    await db.execute(
        sa.text(
            f'UPDATE {_OFFSETS} '  # noqa: S608 内部常量表名
            'SET lease_owner = NULL, lease_until = NULL, updated_at = now() '
            'WHERE consumer_name = :name AND lease_owner = :owner'
        ),
        {'name': consumer_name, 'owner': owner},
    )


async def get_cursor(db: AsyncSession, consumer_name: str) -> int:
    """取消费者当前已确认位点 last_acked_seq（无行=0）。"""
    val = (
        await db.execute(
            sa.text(f'SELECT last_acked_seq FROM {_OFFSETS} WHERE consumer_name = :name'),  # noqa: S608
            {'name': consumer_name},
        )
    ).scalar_one_or_none()
    return int(val or 0)


async def advance_cursor(db: AsyncSession, *, consumer_name: str, to_seq: int) -> None:
    """推进消费者位点到 to_seq（durable 与处理同事务；best-effort 独立事务）。单调不回退。"""
    await db.execute(
        sa.text(
            f'UPDATE {_OFFSETS} SET last_acked_seq = :seq, updated_at = now() '  # noqa: S608
            'WHERE consumer_name = :name AND last_acked_seq < :seq'
        ),
        {'name': consumer_name, 'seq': to_seq},
    )


async def fetch_after(
    db: AsyncSession, *, after_seq: int, shard_key: int, limit: int
) -> list[IntegrationEvent]:
    """按 event_seq 升序取 after_seq 之后的一批事件（消费者顺序拉取）。"""
    rows = (
        await db.execute(
            sa.text(
                'SELECT event_seq, event_id, event_type, aggregate_type, aggregate_id, '
                '       aggregate_seq, shard_key, payload, trace_id, causation_id, occurred_at '
                f'FROM {_EVENTS} '  # noqa: S608 常量表名
                'WHERE shard_key = :sk AND event_seq > :after '
                'ORDER BY event_seq LIMIT :lim'
            ),
            {'sk': shard_key, 'after': after_seq, 'lim': limit},
        )
    ).mappings().all()
    return [
        IntegrationEvent(
            event_seq=int(r['event_seq']),
            event_id=r['event_id'],
            event_type=r['event_type'],
            aggregate_type=r['aggregate_type'],
            aggregate_id=r['aggregate_id'],
            payload=dict(r['payload'] or {}),
            aggregate_seq=(int(r['aggregate_seq']) if r['aggregate_seq'] is not None else None),
            trace_id=r['trace_id'],
            causation_id=r['causation_id'],
            occurred_at=r['occurred_at'],
            shard_key=int(r['shard_key']),
        )
        for r in rows
    ]


async def get_failure(
    db: AsyncSession, *, consumer_name: str, event_seq: int
) -> FailureState | None:
    """取某消费者对某事件的失败态（含 next_attempt_at 是否到期，DB 侧比较 now()）。无=None。"""
    r = (
        await db.execute(
            sa.text(
                'SELECT attempts, next_attempt_at, dead_lettered_at, resolution, '
                '       (next_attempt_at IS NULL OR next_attempt_at <= now()) AS retry_due '
                f'FROM {_FAILURES} WHERE consumer_name = :name AND event_seq = :seq'  # noqa: S608
            ),
            {'name': consumer_name, 'seq': event_seq},
        )
    ).mappings().one_or_none()
    if r is None:
        return None
    return FailureState(
        consumer_name=consumer_name,
        event_seq=event_seq,
        attempts=int(r['attempts']),
        dead_lettered=r['dead_lettered_at'] is not None,
        resolution=r['resolution'],
        retry_due=bool(r['retry_due']),
    )


async def record_retry(
    db: AsyncSession,
    *,
    consumer_name: str,
    event_seq: int,
    attempts: int,
    backoff_seconds: int,
    error: str,
) -> None:
    """记一次可重试失败（UPSERT）：attempts + next_attempt_at=now()+退避 + last_error。"""
    await db.execute(
        sa.text(
            f'INSERT INTO {_FAILURES} '  # noqa: S608 常量表名
            '(consumer_name, event_seq, attempts, next_attempt_at, last_error, created_time, updated_time) '
            # asyncpg 严格类型：int 参数不能拼进字符串再 ::interval，用 int * interval 保持整型
            "VALUES (:name, :seq, :attempts, now() + (:backoff * interval '1 second'), :err, now(), now()) "
            'ON CONFLICT (consumer_name, event_seq) DO UPDATE '
            "SET attempts = :attempts, next_attempt_at = now() + (:backoff * interval '1 second'), "
            '    last_error = :err, updated_time = now()'
        ),
        {'name': consumer_name, 'seq': event_seq, 'attempts': attempts, 'backoff': backoff_seconds, 'err': error},
    )


async def record_dead_letter(
    db: AsyncSession, *, consumer_name: str, event_seq: int, attempts: int, error: str
) -> None:
    """标记进 dead letter（UPSERT）：dead_lettered_at=now()，须显式 resolve 才能推进。"""
    await db.execute(
        sa.text(
            f'INSERT INTO {_FAILURES} '  # noqa: S608 常量表名
            '(consumer_name, event_seq, attempts, dead_lettered_at, last_error, created_time, updated_time) '
            'VALUES (:name, :seq, :attempts, now(), :err, now(), now()) '
            'ON CONFLICT (consumer_name, event_seq) DO UPDATE '
            'SET attempts = :attempts, dead_lettered_at = now(), last_error = :err, updated_time = now()'
        ),
        {'name': consumer_name, 'seq': event_seq, 'attempts': attempts, 'err': error},
    )


async def resolve_dead_letter(
    db: AsyncSession, *, consumer_name: str, event_seq: int, resolution: str
) -> None:
    """处置一个 dead letter（``replayed`` 修复后重放 / ``skipped`` 确认跳过），解除 park。"""
    await db.execute(
        sa.text(
            f'UPDATE {_FAILURES} SET resolution = :res, updated_time = now() '  # noqa: S608
            'WHERE consumer_name = :name AND event_seq = :seq'
        ),
        {'name': consumer_name, 'seq': event_seq, 'res': resolution},
    )


async def retention_low_water(db: AsyncSession, *, durable_consumers: list[str]) -> int | None:
    """retention 低水位 = 所有有效 durable 消费者的最小 last_acked_seq（§7.2）。

    best-effort 消费者不参与。无 durable 消费者行 → None（不设水位）。
    """
    if not durable_consumers:
        return None
    val = (
        await db.execute(
            sa.text(
                f'SELECT MIN(last_acked_seq) FROM {_OFFSETS} '  # noqa: S608 常量表名
                'WHERE consumer_name = ANY(:names)'
            ),
            {'names': durable_consumers},
        )
    ).scalar_one_or_none()
    return int(val) if val is not None else None
