from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class GrowthProjectMigrationQuarantine(HasnGrowthAppBase):
    """获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单"""

    __tablename__ = 'growth_project_migration_quarantine'

    id: Mapped[id_key] = mapped_column(init=False)
    source_table: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    source_record_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    reason_code: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    owner_scope_hint: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment=None)
    user_id_hint: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id_hint: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    details: Mapped[dict] = mapped_column(
        postgresql.JSONB(),
        default_factory=dict,
        comment='只允许原因分类、状态名和稳定资源键，禁止保存联系人明文',
    )
    status: Mapped[str] = mapped_column(sa.String(16), default='pending', comment=None)
    resolution_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    resolved_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    resolved_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
