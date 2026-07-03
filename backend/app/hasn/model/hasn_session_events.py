from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class HasnSessionEvents(Base):
    """HASN 会话事件表"""

    __tablename__ = 'hasn_session_events'

    id: Mapped[id_key] = mapped_column(init=False)
    session_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='会话 ID')
    event_type: Mapped[str] = mapped_column(sa.String(50), default='', comment='事件类型 (session.created/session.paused/task.started/tool.called)')
    event_seq: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='会话内事件序号')
    payload_json: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='事件载荷 (JSON)')
    occurred_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='事件发生时间')
