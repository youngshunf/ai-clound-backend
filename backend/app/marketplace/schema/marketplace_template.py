from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceTemplateSchemaBase(SchemaBase):
    """技能市场模板表（Agent模板/技能包/SOP包）基础模型"""

    template_id: str = Field(description='模板唯一标识')
    namespace: str | None = Field(None, description='命名空间（如 huanxing/clawhub）')
    slug: str | None = Field(None, description='模板标识符')
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
    template_type: str = Field(
        description='模板类型 (agent_template:Agent模板:blue/skill_pack:技能包:cyan/sop_pack:SOP包:purple)',
    )
    name: str = Field(description='模板名称')
    name_en: str | None = Field(None, description='英文名称')
    name_zh: str | None = Field(None, description='中文名称')
    description: str | None = Field(None, description='模板描述')
    description_en: str | None = Field(None, description='英文描述')
    description_zh: str | None = Field(None, description='中文描述')
    source_language: str | None = Field(None, description='源语言（en/zh，用于判断哪个是原文）')
    icon_url: str | None = Field(None, description='模板图标URL')
    emoji: str | None = Field(None, description='emoji图标')
    author_id: int | None = Field(None, description='作者用户ID')
    author_name: str | None = Field(None, description='作者名称')
    pricing_type: str = Field(description='定价类型 (free:免费:green/paid:付费:orange/subscription:订阅:blue)')
    price: Decimal = Field(description='价格')
    is_private: bool = Field(description='是否私有')
    is_official: bool = Field(description='是否官方模板')
    download_count: int = Field(description='下载次数')
    category: str | None = Field(None, description='分类')
    tags: str | None = Field(None, description='标签，逗号分隔')
    name_pool: str | None = Field(None, description='候选人名池，逗号分隔（仅 Agent 模板）')
    source_type: str | None = Field(
        None,
        description='来源类型 (github:GitHub:blue/clawhub:ClawHub:green/local:本地:gray)',
    )
    source_repo_url: str | None = Field(None, description='源仓库 URL')
    source_repo_path: str | None = Field(None, description='仓库内路径')
    skill_dependencies: str | None = Field(None, description='依赖的技能ID列表，逗号分隔')
    sop_dependencies: str | None = Field(None, description='依赖的SOP ID列表，逗号分隔')
    soul_md: str | None = Field(None, description='模板 SOUL.md 内容（Agent 身份档案）')
    agents_md: str | None = Field(None, description='模板 AGENTS.md 内容（Agent 行为指南）')
    user_md: str | None = Field(None, description='模板 USER.md 内容（主人信息种子）')
    memory_md: str | None = Field(None, description='模板 MEMORY.md 内容（Agent 长期/自我演化记忆种子，§ 记录格式）')
    repo_path: str | None = Field(None, description='在 huanxing-hub 中的路径')
    git_commit_hash: str | None = Field(None, description='最新同步的 commit hash')
    synced_at: datetime | None = Field(None, description='最后同步时间')
    translated_at: datetime | None = Field(None, description='最后翻译时间')


class CreateMarketplaceTemplateParam(MarketplaceTemplateSchemaBase):
    """创建技能市场模板表（Agent模板/技能包/SOP包）参数"""


class UpdateMarketplaceTemplateParam(MarketplaceTemplateSchemaBase):
    """更新技能市场模板表（Agent模板/技能包/SOP包）参数"""


class DeleteMarketplaceTemplateParam(SchemaBase):
    """删除技能市场模板表（Agent模板/技能包/SOP包）参数"""

    pks: list[int] = Field(description='技能市场模板表（Agent模板/技能包/SOP包） ID 列表')


class GetMarketplaceTemplateDetail(MarketplaceTemplateSchemaBase):
    """技能市场模板表（Agent模板/技能包/SOP包）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
