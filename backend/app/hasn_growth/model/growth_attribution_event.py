from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class GrowthAttributionEvent(HasnGrowthAppBase):
    """可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实"""

    __tablename__ = 'growth_attribution_event'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    event_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    lead_contact_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    customer_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    opportunity_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    growth_project_playbook_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    playbook_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    playbook_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    source_kind: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    source_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    campaign_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    playbook_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    amount: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    currency: Mapped[str | None] = mapped_column(sa.String(3), default=None, comment=None)
    occurred_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment=None)
