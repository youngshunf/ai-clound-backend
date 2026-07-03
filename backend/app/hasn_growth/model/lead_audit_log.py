import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import UniversalText, id_key


class LeadAuditLog(HasnGrowthAppBase):
    """Lead automation PII and compliance audit log"""

    __tablename__ = 'audit_log'

    id: Mapped[id_key] = mapped_column(init=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    actor_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    actor_role: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    actor_ip: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    actor_ua: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    target_table: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    target_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    target_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    result: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
