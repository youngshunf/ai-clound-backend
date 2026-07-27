"""``hasn_sync.append_event`` 的无业务依赖 SQLAlchemy 适配器。"""

from __future__ import annotations

import json

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef


class SqlAlchemySyncAppender:
    """所有 producer 共用的唯一 sync append 薄封装。"""

    async def append(
        self,
        db: AsyncSession,
        envelope: SyncEnvelope,
    ) -> SyncEventRef:
        """在调用方事务内执行数据库唯一 append 函数。"""
        result = await db.execute(
            sa.text(
                """
                SELECT revision, event_id, deduped
                FROM hasn_sync.append_event(
                    :owner_id,
                    :hasn_id,
                    :event_type,
                    :aggregate_type,
                    :aggregate_id,
                    CAST(:payload AS jsonb),
                    :producer,
                    :source_event_id,
                    :occurred_at
                )
                """
            ),
            {
                'owner_id': envelope.owner_id,
                'hasn_id': envelope.hasn_id,
                'event_type': envelope.event_type,
                'aggregate_type': envelope.aggregate_type,
                'aggregate_id': envelope.aggregate_id,
                'payload': json.dumps(
                    envelope.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(',', ':'),
                    default=str,
                ),
                'producer': envelope.producer,
                'source_event_id': envelope.source_event_id,
                'occurred_at': envelope.occurred_at,
            },
        )
        row = result.mappings().one()
        return SyncEventRef(
            owner_id=envelope.owner_id,
            revision=int(row['revision']),
            event_id=str(row['event_id']),
            event_type=envelope.event_type,
            deduped=bool(row['deduped']),
        )
