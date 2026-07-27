"""通用 owner revision 增量拉取。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.app.hasn_sync.application.full_refresh import require_full_refresh
from backend.app.hasn_sync.domain.cursor import owner_cursor, parse_owner_cursor
from backend.app.hasn_sync.domain.retention import full_refresh_reason
from backend.app.hasn_sync.ports.dto import PullResult


async def pull_events(
    db: AsyncSession,
    *,
    owner_id: str,
    cursor: str | None,
    limit: int,
    store: SQLAlchemySyncStore | None = None,
) -> PullResult:
    """按 owner cursor 拉取通用信封；游标失效时返回 full-refresh。"""
    if limit < 1 or limit > 500:
        raise ValueError('limit 必须在 1..500')
    storage = store or SQLAlchemySyncStore()
    requested = parse_owner_cursor(cursor, owner_id=owner_id)
    bounds = await storage.stream_bounds(db, owner_id=owner_id)
    reason = full_refresh_reason(
        requested_revision=requested,
        min_available_revision=bounds.min_revision,
        head_revision=bounds.head_revision,
    )
    if reason is not None:
        return PullResult(
            events=(),
            next_cursor=owner_cursor(owner_id, requested),
            has_more=False,
            full_refresh=require_full_refresh(
                owner_id=owner_id,
                reason=reason,
                requested_revision=requested,
                min_available_revision=bounds.min_revision,
                head_revision=bounds.head_revision,
            ),
        )

    rows = await storage.pull(
        db,
        owner_id=owner_id,
        after_revision=requested,
        limit=limit + 1,
    )
    events = rows[:limit]
    next_revision = events[-1].revision if events else requested
    return PullResult(
        events=events,
        next_cursor=owner_cursor(owner_id, next_revision),
        has_more=len(rows) > limit,
    )
