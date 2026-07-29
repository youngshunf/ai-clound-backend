from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class GrowthPiiMigrationQuarantine(HasnGrowthAppBase):
    """无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文"""

    __tablename__ = 'growth_pii_migration_quarantine'

    id: Mapped[id_key] = mapped_column(init=False)
    source_table: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    source_record_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    reason_code: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    owner_scope_hint: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment=None)
    user_id_hint: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id_hint: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    field_names: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='仅记录出现 PII 的字段名，不记录原值'
    )
    pii_fingerprint: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='用于重跑去重的带密钥指纹，不是明文或无盐哈希'
    )
    status: Mapped[str] = mapped_column(sa.String(16), default='pending', comment=None)
    resolution_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    resolved_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    resolved_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
