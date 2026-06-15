from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.marketplace.model._base import MarketplaceBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class MarketplaceSkillVersion(MarketplaceBase):
    """技能版本表"""

    __tablename__ = 'marketplace_skill_version'

    id: Mapped[id_key] = mapped_column(init=False)
    skill_id: Mapped[str] = mapped_column(sa.String(255), default='', comment='关联的技能ID')
    version: Mapped[str] = mapped_column(sa.String(50), default='', comment='语义化版本号')
    changelog: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='版本更新日志')
    package_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='完整包下载URL')
    file_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='SHA256校验值')
    content_hash: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='源内容指纹 sha256(规范化SKILL.md+排序文件指纹)，驱动 common_skills_revision；区别于 file_hash(下载包指纹)',
    )
    file_size: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='包大小（字节）')
    is_latest: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否为最新版本')
    published_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='发布时间')
