from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class LeadRawRecord(HasnGrowthAppBase):
    """Raw crawled lead page record"""

    __tablename__ = 'raw_record'

    id: Mapped[id_key] = mapped_column(init=False)
    job_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    source_config_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    firecrawl_request_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    source_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None, comment=None)
    domain: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    title: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    markdown: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    raw_text: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    raw_html: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    raw_payload: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    structured_payload: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    llm_confidence: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    system_score: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    content_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    normalization_version: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
