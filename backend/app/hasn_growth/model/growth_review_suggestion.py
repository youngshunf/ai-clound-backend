from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key


class GrowthReviewSuggestion(HasnGrowthAppBase):
    """下一周期 ICP、渠道与打法建议及 Owner 审阅结果"""

    __tablename__ = 'growth_review_suggestion'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    suggestion_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    proposal: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    evidence: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='建议证据范围、样本量、数据不足和局限，禁止保存联系人明文'
    )
    proposed_by_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    proposed_by_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    applied_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    reviewed_by_owner_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    reviewed_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
