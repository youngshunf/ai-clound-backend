from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class MarketplaceAppVersion(Base):
    """应用版本表"""

    __tablename__ = 'marketplace_app_version'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(100), default='', comment='关联的应用ID')
    version: Mapped[str] = mapped_column(sa.String(50), default='', comment='语义化版本号')
    changelog: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='版本更新日志')
    skill_dependencies_versioned: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='带版本号的技能依赖')
    package_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='完整包下载URL')
    file_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='SHA256校验值')
    file_size: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='包大小（字节）')
    is_latest: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否为最新版本')
    published_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='发布时间')
