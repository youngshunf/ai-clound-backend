"""同步事件 retention 用例。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.app.hasn_sync.ports.dto import RetentionResult
from backend.utils.timezone import timezone


async def run_retention(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    batch_size: int = 1000,
    store: SQLAlchemySyncStore | None = None,
) -> RetentionResult:
    """删除到期事件，同时为每个 owner 至少保留 head 以维护游标边界。"""
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError('batch_size 必须在 1..10000')
    storage = store or SQLAlchemySyncStore()
    deleted = await storage.delete_expired(
        db,
        now=now or timezone.now(),
        batch_size=batch_size,
    )
    return RetentionResult(deleted=deleted)
