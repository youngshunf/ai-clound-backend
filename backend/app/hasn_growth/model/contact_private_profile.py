from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class ContactPrivateProfile(HasnGrowthAppBase):
    """Owner 或企业对全局联系人的私有资料密文"""

    __tablename__ = 'contact_private_profile'

    id: Mapped[id_key] = mapped_column(init=False)
    lead_contact_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    contact_name_ciphertext: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='联系人姓名应用层密文'
    )
    title_ciphertext: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='联系人职位应用层密文')
    encryption_key_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    lawful_basis: Mapped[str] = mapped_column(sa.String(48), default='', comment='本主体取得和使用资料的合法依据')
    source_ref: Mapped[str] = mapped_column(sa.String(255), default='', comment='本主体取得资料的稳定来源引用')
    consent_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    retention_until: Mapped[datetime] = mapped_column(
        TimeZone, default_factory=timezone.now, comment='资料允许保留到期时间'
    )
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment=None)
