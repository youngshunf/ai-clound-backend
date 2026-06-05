from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key


class MarketplaceSkill(Base):
    """技能市场技能表"""

    __tablename__ = 'marketplace_skill'

    id: Mapped[id_key] = mapped_column(init=False)
    skill_id: Mapped[str] = mapped_column(sa.String(255), default='', comment='技能唯一标识')
    namespace: Mapped[str | None] = mapped_column(
        sa.String(160),
        default=None,
        comment='命名空间（如 huanxing/clawhub）',
    )
    slug: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='技能标识符（如 translator-pro）')
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='资源所有者用户ID')
    hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='资源所有者 HASN ID')
    status: Mapped[str] = mapped_column(sa.String(20), default='published', comment='发布状态')
    visibility: Mapped[str] = mapped_column(sa.String(20), default='public', comment='可见性')
    reviewed_by: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='审核人用户ID')
    reviewed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='审核时间')
    review_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='审核备注')
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发布时间')
    suspended_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='封禁时间')
    suspend_reason: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='封禁原因')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='技能名称')
    name_en: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='英文名称')
    name_zh: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='中文名称')
    description_en: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='英文描述')
    description_zh: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='中文描述')
    body_en: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='英文正文（SKILL.md frontmatter 之后的 Markdown 正文）',
    )
    body_zh: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='中文正文（SKILL.md frontmatter 之后的 Markdown 正文）',
    )
    source_language: Mapped[str | None] = mapped_column(
        sa.String(10),
        default=None,
        comment='源语言（en/zh，用于判断哪个是原文）',
    )
    icon_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='SVG图标URL')
    emoji: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='emoji图标')
    author_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='作者用户ID')
    author_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='作者名称')
    category: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='分类')
    tags: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='标签，JSON数组字符串')
    tags_en: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='英文标签，JSON数组字符串')
    tags_zh: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='中文标签，JSON数组字符串')
    files: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='技能目录文件清单，JSON 数组 [{path,size}]（仅名称与大小，不含内容）',
    )
    source_type: Mapped[str | None] = mapped_column(
        sa.String(20),
        default='github',
        comment='来源类型 (huanxing:幻形自研:purple/github:GitHub:blue/clawhub:ClawHub:green)',
    )
    source_repo_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='源仓库 URL')
    source_repo_path: Mapped[str | None] = mapped_column(
        sa.String(500),
        default=None,
        comment='仓库内路径（如 huanxing-skills/productivity/translator-pro）',
    )
    pricing_type: Mapped[str] = mapped_column(
        sa.String(20),
        default='free',
        comment='定价类型 (free:免费:green/paid:付费:orange)',
    )
    price: Mapped[Decimal] = mapped_column(sa.NUMERIC(10, 2), default=0, comment='价格')
    is_private: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否私有')
    is_official: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否官方技能')
    is_common: Mapped[bool] = mapped_column(
        sa.BOOLEAN(),
        default=False,
        comment='是否公共技能（默认叠加进每个 Agent 的技能清单）',
    )
    download_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='下载次数')
    star_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='星标数')
    repo_path: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='在 huanxing-hub 中的路径')
    git_commit_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最新同步的 commit hash')
    synced_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后同步时间')
    translated_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后翻译时间')
