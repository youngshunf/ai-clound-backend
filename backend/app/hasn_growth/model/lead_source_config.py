import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class LeadSourceConfig(HasnGrowthAppBase):
    """AI lead automation source configuration"""

    __tablename__ = 'source_config'

    id: Mapped[id_key] = mapped_column(init=False)
    source_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment=None)
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)
    firecrawl_options: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    min_contact_fields: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    persist_raw_html: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)
    max_html_bytes: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    domain_blacklist: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    country_blacklist: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    rate_limit_per_minute: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    concurrency: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment=None)
