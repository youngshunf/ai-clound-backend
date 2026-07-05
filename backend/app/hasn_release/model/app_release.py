from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_release.model._base import HasnReleaseAppBase
from backend.common.model import id_key, UniversalText, TimeZone


class AppRelease(HasnReleaseAppBase):
    """桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）"""

    __tablename__ = 'app_release'

    id: Mapped[id_key] = mapped_column(init=False)
    version: Mapped[str] = mapped_column(sa.String(32), default='', comment='semver 版本号，如 1.2.0')
    channel: Mapped[str] = mapped_column(sa.String(16), default='', comment='发布渠道 (stable:稳定版:green/beta:内测版:orange)')
    release_notes_md: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='更新日志 Markdown（中）')
    release_notes_en_md: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='更新日志 Markdown（英）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (draft:草稿:gray/published:已发布:green/deprecated:已下线:slate)')
    is_latest: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否当前 channel 最新（置新版时把同 channel 旧版落 false）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (manual:手动上传:blue/github:GitHub构建:purple)')
    github_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联 GitHub Actions run id（source=github 时）')
    published_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发布时刻（status 转 published 时写入）')
