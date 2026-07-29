"""daemon 上行信封只落 sync inbox，不解释业务 payload。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.app.hasn_sync.ports.dto import InboxEnvelope, PushResult


async def accept_envelopes(
    db: AsyncSession,
    envelopes: Sequence[InboxEnvelope],
    *,
    store: SQLAlchemySyncStore | None = None,
) -> PushResult:
    """按通用幂等键接收一批信封；业务应用由外层 worker 另行处理。"""
    storage = store or SQLAlchemySyncStore()
    items = []
    for envelope in envelopes:
        items.append(await storage.accept(db, envelope))
    return PushResult(items=tuple(items))
