from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class MarketplacePersonalSkill(Base):
    """个人技能同步表（个人技能库 SSOT）"""

    __tablename__ = 'marketplace_personal_skill'

    id: Mapped[id_key] = mapped_column(init=False)
    personal_skill_id: Mapped[str] = mapped_column(sa.String(255), default='', comment='个人技能唯一标识（ULID）')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所有者用户 ID')
    hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='所有者 HASN ID')
    slug: Mapped[str] = mapped_column(sa.String(100), default='', comment='技能标识符（owner 内唯一）')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='技能名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='技能描述')
    body: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='SKILL.md frontmatter 之后的 Markdown 正文')
    files: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='技能目录文件清单，JSON 数组 [{path,size}]（仅名称与大小）')
    origin: Mapped[str] = mapped_column(sa.String(20), default='', comment='来源 (runtime-auto:运行时自学:purple/user-upload:本地上传:blue/agent-published:分身发布:green)')
    source_agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='来源分身 HASN ID（runtime-auto/agent-published 时记录）')
    content_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='技能内容哈希（SHA256，去重与同步比对）')
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='版本号（同 slug 内容变更时 +1）')
    package_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='完整包下载 URL（S3 私有桶）')
    file_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='包 SHA256 校验值')
    file_size: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='包大小（字节）')
    icon_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='SVG 图标 URL')
    emoji: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='emoji 图标')
    category: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='分类')
    tags: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='标签，JSON 数组字符串')
    visibility: Mapped[str] = mapped_column(sa.String(20), default='', comment='可见性 (private:私有:gray/public:公开:green)')
    published_skill_id: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='已发布到市场时指向 marketplace_skill.skill_id')
    synced_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后同步时间')
