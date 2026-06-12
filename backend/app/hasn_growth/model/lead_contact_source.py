from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class LeadContactSource(HasnGrowthAppBase):
    """Lead multi-source evidence"""

    __tablename__ = 'contact_source'

    id: Mapped[id_key] = mapped_column(init=False)
    lead_contact_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    raw_record_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    firecrawl_request_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    source_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None, comment=None)
    match_dimension: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    seen_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
