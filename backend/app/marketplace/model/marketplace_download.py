from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.marketplace.model._base import MarketplaceBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class MarketplaceDownload(MarketplaceBase):
    """用户下载记录表"""

    __tablename__ = 'marketplace_download'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户ID')
    resource_type: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='资源类型 (skill:技能:blue/template:模板:cyan)',
    )
    resource_id: Mapped[str] = mapped_column(sa.String(255), default='', comment='资源 ID')
    resource_name: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='资源名称')
    version: Mapped[str] = mapped_column(sa.String(50), default='', comment='下载的版本')
    download_source: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='下载来源（web/api/cli）')
    ip_address: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='IP 地址')
    user_agent: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='User Agent')
    downloaded_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下载时间')
