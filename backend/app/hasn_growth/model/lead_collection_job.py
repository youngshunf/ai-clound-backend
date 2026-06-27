from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class LeadCollectionJob(HasnGrowthAppBase):
    """AI lead automation collection job"""

    __tablename__ = 'collection_job'

    id: Mapped[id_key] = mapped_column(init=False)
    job_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    keyword: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    source_types: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    # user_id = 谁发起的采集（run_job 跑完据此为发起者建 lead_ref）；统一池后不再有 lead_scope。
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(24), default='', comment=None)
    max_pages: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    max_results: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    request_config: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    total_found: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    raw_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    valid_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    invalid_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    duplicate_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    firecrawl_success_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    firecrawl_failed_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
