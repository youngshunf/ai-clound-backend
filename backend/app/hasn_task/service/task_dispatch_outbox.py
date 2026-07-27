"""任务执行帧 transactional outbox 与可靠 relay。"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from dataclasses import dataclass

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.application.outbox_relay import RelayStats
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame, RealtimeGateway

log = logging.getLogger(__name__)

_TABLE = '"hasn_task"."task_dispatch_outbox"'
_METHOD = 'hasn.task.exec'
_LEASE_SECONDS = 120
_MAX_ATTEMPTS = 5
_BACKOFF_SECONDS = (1, 5, 30, 120, 600)


@dataclass(frozen=True, slots=True)
class TaskDispatchRecord:
    """一条已领取的任务执行命令。"""

    command_id: str
    target_owner_id: str
    method: str
    payload: dict
    attempts: int


def _canonical_json(value: dict) -> str:
    """生成稳定 JSON，作为载荷摘要的唯一输入。"""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


async def enqueue_task_exec(
    db: AsyncSession,
    *,
    run_id: int,
    task_id: int,
    target_owner_id: str,
    payload: dict,
) -> str:
    """在任务运行的业务事务内幂等登记执行帧。"""
    if run_id <= 0 or task_id <= 0:
        raise ValueError('任务派发必须提供正数 run_id 和 task_id')
    if not target_owner_id.startswith('h_'):
        raise ValueError('任务派发目标必须是主人 HASN ID')
    if not isinstance(payload, dict):
        raise ValueError('任务派发 payload 必须是对象')

    idempotency_key = f'task:run:{run_id}:exec'
    if payload.get('dispatch_id') != idempotency_key:
        raise ValueError('任务派发 payload.dispatch_id 必须等于 run 派生幂等键')
    canonical_payload = _canonical_json(payload)
    payload_hash = hashlib.sha256(
        f'{target_owner_id}\n{_METHOD}\n{canonical_payload}'.encode()
    ).hexdigest()
    command_id = str(uuid.uuid4())
    inserted = (
        await db.execute(
            sa.text(
                f'INSERT INTO {_TABLE} ('  # noqa: S608 代码内固定表名
                'command_id, run_id, task_id, target_owner_id, method, payload, '
                'payload_hash, idempotency_key, status, attempt_count, next_attempt_at'
                ') VALUES ('
                ':command_id, :run_id, :task_id, :target_owner_id, :method, '
                'CAST(:payload AS jsonb), :payload_hash, :idempotency_key, '
                "'pending', 0, now()"
                ') ON CONFLICT (idempotency_key) DO NOTHING '
                'RETURNING command_id'
            ),
            {
                'command_id': command_id,
                'run_id': run_id,
                'task_id': task_id,
                'target_owner_id': target_owner_id,
                'method': _METHOD,
                'payload': canonical_payload,
                'payload_hash': payload_hash,
                'idempotency_key': idempotency_key,
            },
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return str(inserted)

    existing = (
        await db.execute(
            sa.text(
                f'SELECT command_id, payload_hash FROM {_TABLE} '  # noqa: S608
                'WHERE idempotency_key = :idempotency_key'
            ),
            {'idempotency_key': idempotency_key},
        )
    ).one_or_none()
    if existing is None:
        raise RuntimeError('任务派发幂等写入后无法读取命令')
    if str(existing.payload_hash).strip() != payload_hash:
        raise ValueError(f'任务派发幂等键冲突：{idempotency_key}')
    return str(existing.command_id)


class TaskDispatchOutboxStore:
    """任务域自有 outbox 的租约存储。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        instance_id: str,
        lease_seconds: int = _LEASE_SECONDS,
    ) -> None:
        if not instance_id:
            raise ValueError('任务派发 relay instance_id 不能为空')
        self._session_factory = session_factory
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds

    async def claim_batch(self, *, limit: int, now: int) -> list[TaskDispatchRecord]:
        """领取到期命令，并接管已过期的 processing 租约。"""
        if limit <= 0:
            return []
        async with self._session_factory.begin() as db:
            rows = (
                await db.execute(
                    sa.text(
                        'WITH candidates AS ('
                        f' SELECT id FROM {_TABLE}'  # noqa: S608
                        ' WHERE ('
                        "   (status = 'pending' AND next_attempt_at <= to_timestamp(:now))"
                        '   OR '
                        "   (status = 'processing' AND lease_until <= to_timestamp(:now))"
                        ' )'
                        ' ORDER BY id'
                        ' LIMIT :limit'
                        ' FOR UPDATE SKIP LOCKED'
                        ') '
                        f'UPDATE {_TABLE} AS outbox '  # noqa: S608
                        "SET status = 'processing', "
                        'lease_until = to_timestamp(:now) + make_interval(secs => :lease_seconds), '
                        'locked_by = :instance_id, updated_time = now() '
                        'FROM candidates '
                        'WHERE outbox.id = candidates.id '
                        'RETURNING outbox.command_id, outbox.target_owner_id, '
                        'outbox.method, outbox.payload, outbox.attempt_count'
                    ),
                    {
                        'now': now,
                        'limit': limit,
                        'lease_seconds': self._lease_seconds,
                        'instance_id': self._instance_id,
                    },
                )
            ).all()
        return [
            TaskDispatchRecord(
                command_id=str(row.command_id),
                target_owner_id=str(row.target_owner_id),
                method=str(row.method),
                payload=dict(row.payload),
                attempts=int(row.attempt_count),
            )
            for row in rows
        ]

    async def mark_completed(self, command_id: str) -> None:
        """仅由当前租约持有者确认完成，重复确认保持幂等。"""
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {_TABLE} '  # noqa: S608
                    "SET status = 'completed', "
                    'completed_at = COALESCE(completed_at, now()), '
                    'lease_until = NULL, locked_by = NULL, last_error = NULL, '
                    'updated_time = now() '
                    'WHERE command_id = :command_id AND ('
                    "(status = 'processing' AND locked_by = :instance_id) "
                    "OR status = 'completed'"
                    ')'
                ),
                {'command_id': command_id, 'instance_id': self._instance_id},
            )

    async def mark_retry(
        self,
        command_id: str,
        *,
        error: str,
        attempts: int,
        next_attempt_at: int,
    ) -> None:
        """记录可恢复失败并释放租约。"""
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {_TABLE} '  # noqa: S608
                    "SET status = 'pending', attempt_count = :attempts, "
                    'next_attempt_at = to_timestamp(:next_attempt_at), '
                    'lease_until = NULL, locked_by = NULL, last_error = :error, '
                    'updated_time = now() '
                    "WHERE command_id = :command_id AND status = 'processing' "
                    'AND locked_by = :instance_id'
                ),
                {
                    'command_id': command_id,
                    'instance_id': self._instance_id,
                    'attempts': attempts,
                    'next_attempt_at': next_attempt_at,
                    'error': error,
                },
            )

    async def mark_dead_letter(
        self,
        command_id: str,
        *,
        error: str,
        attempts: int,
    ) -> None:
        """记录终局失败并释放租约。"""
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {_TABLE} '  # noqa: S608
                    "SET status = 'dead_letter', attempt_count = :attempts, "
                    'lease_until = NULL, locked_by = NULL, last_error = :error, '
                    'updated_time = now() '
                    "WHERE command_id = :command_id AND status = 'processing' "
                    'AND locked_by = :instance_id'
                ),
                {
                    'command_id': command_id,
                    'instance_id': self._instance_id,
                    'attempts': attempts,
                    'error': error,
                },
            )


class TaskDispatchRelay:
    """把任务执行 outbox 可靠交给实时投递端口。"""

    def __init__(
        self,
        *,
        store: TaskDispatchOutboxStore,
        gateway: RealtimeGateway,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._max_attempts = max_attempts

    async def drain_once(self, *, now: int, batch_limit: int = 50) -> RelayStats:
        """投递一批任务执行命令；单条失败不阻塞同批其他命令。"""
        stats = RelayStats()
        records = await self._store.claim_batch(limit=batch_limit, now=now)
        stats.claimed = len(records)
        for record in records:
            try:
                if record.method != _METHOD:
                    raise ValueError(f'不支持的任务派发方法：{record.method}')
                await self._gateway.push_to_owner(
                    record.target_owner_id,
                    RealtimeFrame(method=record.method, params=record.payload),
                )
            except Exception as exc:  # noqa: BLE001 relay 必须持久化任意投递故障
                await self._handle_failure(record, exc, now=now, stats=stats)
                continue
            await self._store.mark_completed(record.command_id)
            stats.completed += 1
        return stats

    async def _handle_failure(
        self,
        record: TaskDispatchRecord,
        error: Exception,
        *,
        now: int,
        stats: RelayStats,
    ) -> None:
        """按统一次数与退避规则处理任务派发失败。"""
        attempts = record.attempts + 1
        diagnostic = f'任务执行帧投递失败：{error!r}'
        if attempts >= self._max_attempts:
            await self._store.mark_dead_letter(
                record.command_id,
                error=diagnostic,
                attempts=attempts,
            )
            stats.dead_lettered += 1
            log.error(
                '任务派发命令进 dead letter（command=%s attempts=%d）：%s',
                record.command_id,
                attempts,
                diagnostic,
            )
            return
        backoff = _BACKOFF_SECONDS[min(attempts, len(_BACKOFF_SECONDS)) - 1]
        await self._store.mark_retry(
            record.command_id,
            error=diagnostic,
            attempts=attempts,
            next_attempt_at=now + backoff,
        )
        stats.retried += 1
        log.warning(
            '任务派发失败将退避重试（command=%s attempts=%d next=+%ds）：%s',
            record.command_id,
            attempts,
            backoff,
            diagnostic,
        )


def build_task_dispatch_relay(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    gateway: RealtimeGateway,
    instance_id: str,
) -> TaskDispatchRelay:
    """装配任务生产方 relay。"""
    return TaskDispatchRelay(
        store=TaskDispatchOutboxStore(
            session_factory=session_factory,
            instance_id=instance_id,
        ),
        gateway=gateway,
    )
