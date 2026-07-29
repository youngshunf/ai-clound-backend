from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key


class GrowthProfileSuggestion(HasnGrowthAppBase):
    """分身或系统提出、等待主人确认的画像建议"""

    __tablename__ = 'growth_profile_suggestion'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    expected_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    product_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    icp_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    knowledge_document_versions: Mapped[list[dict]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment=None
    )
    source_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    proposed_by_kind: Mapped[str] = mapped_column(
        sa.String(16),
        default='',
        comment='建议主体 (agent:AI分身:purple/system:系统:gray)',
    )
    proposed_by_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    trace_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='',
        comment=('状态 (pending:待确认:orange/accepted:已接受:green/rejected:已拒绝:red/stale:版本冲突:gray)'),
    )
    reviewed_by_owner_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    reviewed_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
