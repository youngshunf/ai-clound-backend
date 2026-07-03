from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnSyncEvents(Base):
    """HASN 服务端下行同步事件表"""

    __tablename__ = 'hasn_sync_events'

    id: Mapped[id_key] = mapped_column(init=False)
    event_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件唯一 ID (se_{uuid})')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件所属 Owner hasn_id')
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件目标主体 hasn_id（Human 或 owned Agent）')
    event_type: Mapped[str] = mapped_column(sa.String(50), default='', comment='事件类型 (message_created:消息创建:blue/inbox_updated:Inbox更新:green/profile_updated:Profile更新:orange/runtime_warning:Runtime警告:purple/channel_bound:渠道绑定:cyan)')
    aggregate_type: Mapped[str] = mapped_column(sa.String(40), default='', comment='聚合类型 (message:消息:blue/conversation:会话:green/profile:Profile:orange/runtime:Runtime:purple/channel:渠道:cyan/sandbox:沙箱:gray)')
    aggregate_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='聚合 ID')
    conversation_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='关联会话 ID（如有）')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='事件载荷（服务端权威摘要，不含 Runtime 私有本地态）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='Owner 维度单调递增 revision')
    occurred_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='事件发生时间')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='事件保留到期时间（可空）')
