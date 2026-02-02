from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class AppVersion(Base):
    """应用版本表"""

    __tablename__ = 'app_version'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(50), default='', comment='应用标识 (creator-flow:CreatorFlow/craft-agent:CraftAgent)')
    version: Mapped[str] = mapped_column(sa.String(50), default='', comment='语义化版本号')
    platform: Mapped[str] = mapped_column(sa.String(30), default='', comment='平台 (darwin:macOS/win32:Windows/linux:Linux)')
    arch: Mapped[str] = mapped_column(sa.String(20), default='', comment='架构 (x64:x64/arm64:ARM64)')
    download_url: Mapped[str] = mapped_column(sa.String(500), default='', comment='下载地址')
    file_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='SHA256 校验值')
    file_size: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='文件大小（字节）')
    filename: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='文件名')
    changelog: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='更新日志')
    min_version: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='最小兼容版本')
    is_force_update: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否强制更新')
    is_latest: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否为最新版本')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (draft:草稿:blue/published:已发布:green/deprecated:已废弃:orange)')
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发布时间')
