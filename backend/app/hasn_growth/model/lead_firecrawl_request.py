from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class LeadFirecrawlRequest(HasnGrowthAppBase):
    """Firecrawl request audit for AI lead automation"""

    __tablename__ = 'firecrawl_request'

    id: Mapped[id_key] = mapped_column(init=False)
    job_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    source_config_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    endpoint: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    target_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None, comment=None)
    query_data: Mapped[str | None] = mapped_column('query',sa.String(500), default=None, comment=None)
    request_payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    extract_mode: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    llm_schema_version: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    llm_prompt_version: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    response_status: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    duration_ms: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    result_count: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    response_excerpt: Mapped[str | None] = mapped_column(sa.String(4096), default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
