from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class OutreachMessageEvent(HasnGrowthAppBase):
    """触达审批、投递、拦截、人工证明和回复的追加式事件"""

    __tablename__ = 'outreach_message_event'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    outreach_message_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    event_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    occurred_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    actor_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    actor_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    approval_status: Mapped[str | None] = mapped_column(sa.String(24), default=None, comment=None)
    delivery_status: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    approval_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    content_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    error_class: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment=None)
