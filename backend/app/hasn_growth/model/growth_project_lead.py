from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class GrowthProjectLead(HasnGrowthAppBase):
    """获客漏斗对全局联系人事实的项目级引用"""

    __tablename__ = 'growth_project_lead'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    lead_contact_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment=None)
    source_kind: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    source_tool: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    source_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    source_meta: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='new', comment=None)
    dismiss_reason: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    match_score: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    score_breakdown: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    scoring_version: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    evidence_fresh_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    acquired_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
