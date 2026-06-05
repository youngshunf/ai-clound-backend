from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceSkillSchemaBase(SchemaBase):
    """技能市场技能基础模型"""
    skill_id: str = Field(description='技能唯一标识')
    namespace: str | None = Field(None, description='命名空间（如 huanxing/clawhub）')
    slug: str | None = Field(None, description='技能标识符（如 translator-pro）')
    user_id: int | None = Field(None, description='资源所有者用户ID')
    hasn_id: str | None = Field(None, description='资源所有者 HASN ID')
    status: str = Field('published', description='发布状态')
    visibility: str = Field('public', description='可见性')
    reviewed_by: int | None = Field(None, description='审核人用户ID')
    reviewed_at: datetime | None = Field(None, description='审核时间')
    review_note: str | None = Field(None, description='审核备注')
    published_at: datetime | None = Field(None, description='发布时间')
    suspended_at: datetime | None = Field(None, description='封禁时间')
    suspend_reason: str | None = Field(None, description='封禁原因')
    name: str = Field('', description='技能名称')
    name_en: str | None = Field(None, description='英文名称')
    name_zh: str | None = Field(None, description='中文名称')
    description_en: str | None = Field(None, description='英文描述')
    description_zh: str | None = Field(None, description='中文描述')
    body_en: str | None = Field(None, description='英文正文（SKILL.md frontmatter 之后的 Markdown 正文）')
    body_zh: str | None = Field(None, description='中文正文（SKILL.md frontmatter 之后的 Markdown 正文）')
    source_language: str | None = Field(None, description='源语言（en/zh，用于判断哪个是原文）')
    icon_url: str | None = Field(None, description='SVG图标URL')
    emoji: str | None = Field(None, description='emoji图标')
    author_id: int | None = Field(None, description='作者用户ID')
    author_name: str | None = Field(None, description='作者名称')
    category: str | None = Field(None, description='分类')
    tags: str | None = Field(None, description='默认标签，JSON数组字符串')
    tags_en: str | None = Field(None, description='英文标签，JSON数组字符串')
    tags_zh: str | None = Field(None, description='中文标签，JSON数组字符串')
    source_type: str | None = Field(
        'github',
        description='来源类型 (huanxing:幻形自研:purple/github:GitHub:blue/clawhub:ClawHub:green)',
    )
    source_repo_url: str | None = Field(None, description='源仓库 URL')
    source_repo_path: str | None = Field(
        None,
        description='源仓库内路径（如 huanxing-skills/productivity/translator-pro）',
    )
    repo_path: str | None = Field(None, description='在 huanxing-hub 中的路径')
    pricing_type: str = Field(description='定价类型 (free:免费:green/paid:付费:orange)')
    price: Decimal = Field(description='价格')
    is_private: bool = Field(description='是否私有')
    is_official: bool = Field(description='是否官方技能')
    is_common: bool = Field(False, description='是否公共技能（默认叠加进每个 Agent 的技能清单）')
    download_count: int = Field(description='下载次数')
    star_count: int = Field(0, description='星标数')
    git_commit_hash: str | None = Field(None, description='最新同步的 commit hash')
    synced_at: datetime | None = Field(None, description='最后同步时间')
    translated_at: datetime | None = Field(None, description='最后翻译时间')


class CreateMarketplaceSkillParam(MarketplaceSkillSchemaBase):
    """创建技能市场技能参数"""


class UpdateMarketplaceSkillParam(MarketplaceSkillSchemaBase):
    """更新技能市场技能参数"""


class DeleteMarketplaceSkillParam(SchemaBase):
    """删除技能市场技能参数"""

    pks: list[int] = Field(description='技能市场技能 ID 列表')


class GetMarketplaceSkillDetail(MarketplaceSkillSchemaBase):
    """技能市场技能详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    latest_version: str | None = Field(None, description='最新版本号')
    created_time: datetime
    updated_time: datetime | None = None
