from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class LeadRejectedRecord(HasnGrowthAppBase):
    """Rejected, invalid, duplicate, or failed lead record"""

    __tablename__ = 'rejected_record'

    id: Mapped[id_key] = mapped_column(init=False)
    job_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    raw_record_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    firecrawl_request_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_type: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    source_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None, comment=None)
    reason: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    email: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    phone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    raw_excerpt: Mapped[str | None] = mapped_column(sa.String(4096), default=None, comment=None)
    duplicate_contact_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
