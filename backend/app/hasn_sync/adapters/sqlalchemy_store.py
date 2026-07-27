"""hasn_sync 事件流、inbox 与 retention 的 SQLAlchemy 存储实现。"""

from __future__ import annotations

import json

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.ports.dto import (
    ClaimedInboxEvent,
    InboxAcceptance,
    InboxEnvelope,
    StoredSyncEvent,
)
from backend.database.schema_names import SCHEMA_NAMES


_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


@dataclass(frozen=True)
class StreamBounds:
    """某 owner 当前仍保留的 revision 边界。"""

    min_revision: int
    head_revision: int


@dataclass(slots=True)
class SQLAlchemySyncStore:
    """只操作 hasn_sync schema 的通用存储。"""

    async def stream_bounds(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
    ) -> StreamBounds:
        row = (
            await db.execute(
                sa.text(
                    f'SELECT COALESCE(MIN(revision), 0) AS min_revision, '
                    f'COALESCE(MAX(revision), 0) AS head_revision '
                    f'FROM {_EVENTS} WHERE owner_id = :owner_id'
                ),
                {'owner_id': owner_id},
            )
        ).mappings().one()
        return StreamBounds(
            min_revision=int(row['min_revision']),
            head_revision=int(row['head_revision']),
        )

    async def pull(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        after_revision: int,
        limit: int,
    ) -> tuple[StoredSyncEvent, ...]:
        rows = (
            await db.execute(
                sa.text(
                    f'SELECT event_id, event_type, revision, occurred_at, payload '
                    f'FROM {_EVENTS} '
                    'WHERE owner_id = :owner_id AND revision > :after_revision '
                    'ORDER BY revision ASC LIMIT :limit'
                ),
                {
                    'owner_id': owner_id,
                    'after_revision': after_revision,
                    'limit': limit,
                },
            )
        ).mappings().all()
        return tuple(
            StoredSyncEvent(
                event_id=str(row['event_id']),
                event_type=str(row['event_type']),
                revision=int(row['revision']),
                occurred_at=row['occurred_at'],
                payload=dict(row['payload'] or {}),
            )
            for row in rows
        )

    async def accept(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
    ) -> InboxAcceptance:
        payload_json = json.dumps(
            envelope.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        )
        inserted = (
            await db.execute(
                sa.text(
                    f'INSERT INTO {_INBOX} ('
                    'client_event_id, owner_id, hasn_id, node_id, event_type, '
                    'payload, dedupe_key, status, received_at, created_time, updated_time'
                    ') VALUES ('
                    ':client_event_id, :owner_id, :hasn_id, :node_id, :event_type, '
                    'CAST(:payload AS jsonb), :dedupe_key, :status, now(), now(), now()'
                    ') ON CONFLICT (owner_id, node_id, client_event_id) DO NOTHING '
                    'RETURNING id'
                ),
                {
                    'client_event_id': envelope.client_event_id,
                    'owner_id': envelope.owner_id,
                    'hasn_id': envelope.hasn_id,
                    'node_id': envelope.node_id,
                    'event_type': envelope.event_type,
                    'payload': payload_json,
                    'dedupe_key': envelope.dedupe_key,
                    'status': 'accepted',
                },
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return InboxAcceptance(
                client_event_id=envelope.client_event_id,
                status='accepted',
            )

        existing = (
            await db.execute(
                sa.text(
                    f'SELECT hasn_id, event_type, payload, dedupe_key '
                    f'FROM {_INBOX} '
                    'WHERE owner_id = :owner_id '
                    'AND node_id = :node_id '
                    'AND client_event_id = :client_event_id'
                ),
                {
                    'owner_id': envelope.owner_id,
                    'node_id': envelope.node_id,
                    'client_event_id': envelope.client_event_id,
                },
            )
        ).mappings().one()
        same = (
            existing['hasn_id'] == envelope.hasn_id
            and existing['event_type'] == envelope.event_type
            and dict(existing['payload'] or {}) == envelope.payload
            and existing['dedupe_key'] == envelope.dedupe_key
        )
        return InboxAcceptance(
            client_event_id=envelope.client_event_id,
            status='duplicate' if same else 'conflict',
        )

    async def claim_inbox(
        self,
        db: AsyncSession,
        *,
        instance_id: str,
        now: datetime,
        lease_seconds: int,
        event_types: Sequence[str],
    ) -> ClaimedInboxEvent | None:
        """原子领取一条到期或租约失效的 inbox 事件。"""
        if not instance_id or len(instance_id) > 64:
            raise ValueError('worker instance_id 必须为 1 至 64 个字符')
        if lease_seconds < 1:
            raise ValueError('worker lease_seconds 必须大于 0')
        claimed_types = tuple(dict.fromkeys(event_types))
        if not claimed_types:
            return None
        row = (
            await db.execute(
                sa.text(
                    f'WITH candidate AS ('
                    f'  SELECT source.id FROM {_INBOX} source '
                    '  WHERE source.applied_at IS NULL '
                    '    AND source.dead_at IS NULL '
                    '    AND source.event_type = ANY(CAST(:event_types AS text[])) '
                    '    AND ('
                    "      source.status = 'accepted' "
                    "      OR (source.status = 'retry' "
                    '          AND COALESCE(source.next_attempt_at, source.received_at) <= :now) '
                    "      OR (source.status = 'processing' "
                    '          AND COALESCE(source.locked_at, source.received_at) '
                    "              <= :now - CAST(:lease_seconds AS integer) * interval '1 second')"
                    '    ) '
                    '  ORDER BY source.received_at, source.id '
                    '  FOR UPDATE SKIP LOCKED '
                    '  LIMIT 1'
                    ') '
                    f'UPDATE {_INBOX} target '
                    "SET status = 'processing', "
                    '    attempt_count = target.attempt_count + 1, '
                    '    locked_by = :instance_id, '
                    '    locked_at = :now, '
                    '    next_attempt_at = NULL, '
                    '    updated_time = :now '
                    'FROM candidate '
                    'WHERE target.id = candidate.id '
                    'RETURNING target.id, target.owner_id, target.node_id, '
                    'target.client_event_id, target.hasn_id, target.event_type, '
                    'target.payload, target.dedupe_key, target.attempt_count'
                ),
                {
                    'instance_id': instance_id,
                    'now': now,
                    'lease_seconds': lease_seconds,
                    'event_types': list(claimed_types),
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        envelope = InboxEnvelope(
            owner_id=str(row['owner_id']),
            node_id=str(row['node_id']),
            client_event_id=str(row['client_event_id']),
            hasn_id=str(row['hasn_id']),
            event_type=str(row['event_type']),
            payload=dict(row['payload'] or {}),
            dedupe_key=row['dedupe_key'],
        )
        return ClaimedInboxEvent(
            row_id=int(row['id']),
            envelope=envelope,
            attempt_count=int(row['attempt_count']),
            idempotency_key=(
                f"sync-inbox:{envelope.owner_id}:{envelope.node_id}:"
                f'{envelope.client_event_id}'
            ),
            locked_by=instance_id,
        )

    async def mark_inbox_applied(
        self,
        db: AsyncSession,
        claimed: ClaimedInboxEvent,
        *,
        now: datetime,
    ) -> None:
        """仅允许持有当前租约的 worker 写入成功 ACK。"""
        result = await db.execute(
            sa.text(
                f'UPDATE {_INBOX} '
                "SET status = 'applied', applied_at = :now, "
                '    locked_by = NULL, locked_at = NULL, '
                '    next_attempt_at = NULL, last_error = NULL, '
                '    updated_time = :now '
                'WHERE id = :row_id '
                "  AND status = 'processing' "
                '  AND locked_by = :locked_by'
            ),
            {
                'now': now,
                'row_id': claimed.row_id,
                'locked_by': claimed.locked_by,
            },
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise RuntimeError('sync inbox ACK 失败：租约已丢失或状态已变化')

    async def mark_inbox_failed(
        self,
        db: AsyncSession,
        claimed: ClaimedInboxEvent,
        *,
        error: str,
        now: datetime,
        max_attempts: int,
    ) -> str:
        """记录一次业务应用失败；重试耗尽时进入 dead。"""
        if max_attempts < 1:
            raise ValueError('max_attempts 必须大于 0')
        terminal = claimed.attempt_count >= max_attempts
        delay_seconds = min(2 ** max(claimed.attempt_count - 1, 0), 300)
        status = 'dead' if terminal else 'retry'
        result = await db.execute(
            sa.text(
                f'UPDATE {_INBOX} '
                'SET status = :status, '
                '    next_attempt_at = CASE WHEN :terminal '
                '      THEN CAST(NULL AS timestamptz) '
                '      ELSE CAST(:now AS timestamptz) '
                "        + CAST(:delay_seconds AS integer) * interval '1 second' END, "
                '    dead_at = CASE WHEN :terminal '
                '      THEN CAST(:now AS timestamptz) '
                '      ELSE CAST(NULL AS timestamptz) END, '
                '    last_error = :last_error, '
                '    locked_by = NULL, locked_at = NULL, '
                '    updated_time = :now '
                'WHERE id = :row_id '
                "  AND status = 'processing' "
                '  AND locked_by = :locked_by'
            ),
            {
                'status': status,
                'terminal': terminal,
                'now': now,
                'delay_seconds': delay_seconds,
                'last_error': error[:2000],
                'row_id': claimed.row_id,
                'locked_by': claimed.locked_by,
            },
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise RuntimeError('sync inbox 失败回执写入失败：租约已丢失或状态已变化')
        return status

    async def delete_expired(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        batch_size: int,
    ) -> int:
        rows = (
            await db.execute(
                sa.text(
                    f'WITH victims AS ('
                    f'  SELECT e.id FROM {_EVENTS} e '
                    '  WHERE e.expires_at IS NOT NULL '
                    '    AND e.expires_at <= :now '
                    '    AND e.revision < ('
                    f'      SELECT MAX(latest.revision) FROM {_EVENTS} latest '
                    '      WHERE latest.owner_id = e.owner_id'
                    '    ) '
                    '  ORDER BY e.expires_at, e.id '
                    '  LIMIT :batch_size '
                    '  FOR UPDATE SKIP LOCKED'
                    ') '
                    f'DELETE FROM {_EVENTS} target USING victims '
                    'WHERE target.id = victims.id RETURNING target.id'
                ),
                {'now': now, 'batch_size': batch_size},
            )
        ).scalars().all()
        return len(rows)
