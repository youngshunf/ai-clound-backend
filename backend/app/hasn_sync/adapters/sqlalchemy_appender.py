"""hasn_sync.adapters.sqlalchemy_appender · SyncAppender 的 SQLAlchemy 实现

薄封装现网 `SqlAlchemySyncGateway._append_sync_event_with_id` 单 chokepoint。R2-07 起该
chokepoint 内部已改为 `SELECT hasn_sync.append_event(...)` PG 函数（per-owner
`pg_advisory_xact_lock` 串行化 gapless revision 分配 + `(owner_id, producer, source_event_id)`
幂等去重），本 adapter 透传 `producer`/`source_event_id`/`occurred_at` 并回吐 `deduped`，
port/DTO 形状不变（§3.2 单实现：所有写入方共此唯一 append）。

依赖方向（§0.1）：adapter 层**允许**依赖现网 service（收编期过渡）；业务模块只认
`hasn_sync.ports.SyncAppender` 抽象，不直接 import 本 adapter。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef


@dataclass(slots=True)
class SqlAlchemySyncAppender:
    """SyncAppender 的实现（封装现网 chokepoint→hasn_sync.append_event，与业务写同事务）。"""

    # 现网无状态网关，默认自建一份；测试可注入替身。
    gateway: SqlAlchemySyncGateway = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.gateway is None:
            self.gateway = SqlAlchemySyncGateway()

    async def append(self, db: AsyncSession, envelope: SyncEnvelope) -> SyncEventRef:
        """在 db 事务内追加事件；revision 由 hasn_sync.append_event 的 advisory-lock 逻辑分配。"""
        revision, event_id, deduped = await self.gateway._append_sync_event_with_id(
            db,
            owner_id=envelope.owner_id,
            hasn_id=envelope.hasn_id,
            event_type=envelope.event_type,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            payload=envelope.payload,
            producer=envelope.producer,
            source_event_id=envelope.source_event_id,
            occurred_at=envelope.occurred_at,
        )
        return SyncEventRef(
            owner_id=envelope.owner_id,
            revision=revision,
            event_id=event_id,
            event_type=envelope.event_type,
            deduped=deduped,
        )
