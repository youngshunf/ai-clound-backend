from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class ContactChannel(HasnGrowthAppBase):
    """Owner 或企业授权持有的联系方式密文与版本化 HMAC"""

    __tablename__ = 'contact_channel'

    id: Mapped[id_key] = mapped_column(init=False)
    private_profile_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    lead_contact_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    channel: Mapped[str] = mapped_column(sa.String(24), default='', comment=None)
    value_ciphertext: Mapped[str] = mapped_column(
        UniversalText, default='', comment='联系方式应用层密文，禁止进入 Agent、日志和 daemon 缓存'
    )
    encryption_key_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    value_hmac: Mapped[str] = mapped_column(
        sa.String(128), default='', comment='使用独立服务端 HMAC 密钥计算的渠道匹配值'
    )
    hash_key_version: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='HMAC 密钥版本，轮换期支持多版本匹配'
    )
    lawful_basis: Mapped[str] = mapped_column(sa.String(48), default='', comment=None)
    source_ref: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    consent_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    verified_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    fresh_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    retention_until: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment=None)
