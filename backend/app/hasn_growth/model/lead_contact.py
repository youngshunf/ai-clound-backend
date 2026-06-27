from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class LeadContact(HasnGrowthAppBase):
    """Valid deduplicated lead contact"""

    __tablename__ = 'contact'

    id: Mapped[id_key] = mapped_column(init=False)
    lead_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    # 统一线索池：pool_visibility 区分公共可匹配/私有。归属与用户级状态全部下沉 lead_ref 引用表。
    pool_visibility: Mapped[str] = mapped_column(sa.String(16), default='public', comment=None)
    # 过渡列（已弃用）：统一池后线索不再按 user_id 归属（归属=lead_ref）。仍被 codegen CRUD 面
    # （/api/v1/growth/lead/contacts app/agent/open 按行归属判定）引用，待该面去留决策后随迁移 drop。
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    company_name: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    contact_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    email: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    email_normalized: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    phone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    phone_normalized: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment=None)
    website: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    domain: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    country: Mapped[str | None] = mapped_column(sa.String(8), default=None, comment=None)
    region: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    city: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    address: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    industry: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    source_type: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    source_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None, comment=None)
    keyword: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    confidence_score: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    dedupe_key_email: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    dedupe_key_phone: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    dedupe_key_domain: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    normalization_version: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    first_seen_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    last_seen_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    last_exported_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    archived_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment=None)
