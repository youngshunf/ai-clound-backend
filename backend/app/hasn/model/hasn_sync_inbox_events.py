from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnSyncInboxEvents(Base):
    """HASN 客户端上行 outbox 幂等/冲突表"""

    __tablename__ = 'hasn_sync_inbox_events'

    id: Mapped[id_key] = mapped_column(init=False)
    client_event_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='客户端事件 ID')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件所属 Owner hasn_id')
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件主体 hasn_id（Human 或 owned Agent）')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上报 Node ID')
    event_type: Mapped[str] = mapped_column(sa.String(50), default='', comment='事件类型 (ack:确认:green/read:已读:blue/edit:编辑:orange/recall:撤回:red/local_state:本地状态:gray)')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='客户端上行载荷（不得包含 workspace/endpoint/PID/CLI args/OAuth path）')
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='业务幂等键')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='处理状态 (accepted:已接收:blue/applied:已应用:green/conflict:冲突:orange/rejected:已拒绝:red)')
    server_revision: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='对应服务端 revision')
    conflict_reason: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='冲突原因')
    received_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='服务端接收时间')
