from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class MarketplaceDownload(Base):
    """用户下载记录表"""

    __tablename__ = 'marketplace_download'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户ID')
    item_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='类型 (app:应用/skill:技能)')
    item_id: Mapped[str] = mapped_column(sa.String(100), default='', comment='应用或技能ID')
    version: Mapped[str] = mapped_column(sa.String(50), default='', comment='下载的版本')
    downloaded_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下载时间')
