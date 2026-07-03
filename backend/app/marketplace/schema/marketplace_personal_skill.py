from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplacePersonalSkillSchemaBase(SchemaBase):
    """个人技能同步表（个人技能库 SSOT）基础模型"""
    personal_skill_id: str = Field(description='个人技能唯一标识（ULID）')
    user_id: int = Field(description='所有者用户 ID')
    hasn_id: str | None = Field(None, description='所有者 HASN ID')
    slug: str = Field(description='技能标识符（owner 内唯一）')
    name: str = Field(description='技能名称')
    description: str | None = Field(None, description='技能描述')
    body: str | None = Field(None, description='SKILL.md frontmatter 之后的 Markdown 正文')
    files: str | None = Field(None, description='技能目录文件清单，JSON 数组 [{path,size}]（仅名称与大小）')
    origin: str = Field(description='来源 (runtime-auto:运行时自学:purple/user-upload:本地上传:blue/agent-published:分身发布:green)')
    source_agent_hasn_id: str | None = Field(None, description='来源分身 HASN ID（runtime-auto/agent-published 时记录）')
    content_hash: str | None = Field(None, description='技能内容哈希（SHA256，去重与同步比对）')
    version: int = Field(description='版本号（同 slug 内容变更时 +1）')
    package_url: str | None = Field(None, description='完整包下载 URL（S3 私有桶）')
    file_hash: str | None = Field(None, description='包 SHA256 校验值')
    file_size: int | None = Field(None, description='包大小（字节）')
    icon_url: str | None = Field(None, description='SVG 图标 URL')
    emoji: str | None = Field(None, description='emoji 图标')
    category: str | None = Field(None, description='分类')
    tags: str | None = Field(None, description='标签，JSON 数组字符串')
    visibility: str = Field(description='可见性 (private:私有:gray/public:公开:green)')
    published_skill_id: str | None = Field(None, description='已发布到市场时指向 marketplace_skill.skill_id')
    synced_at: datetime | None = Field(None, description='最后同步时间')


class CreateMarketplacePersonalSkillParam(MarketplacePersonalSkillSchemaBase):
    """创建个人技能同步表（个人技能库 SSOT）参数"""


class UpdateMarketplacePersonalSkillParam(MarketplacePersonalSkillSchemaBase):
    """更新个人技能同步表（个人技能库 SSOT）参数"""


class DeleteMarketplacePersonalSkillParam(SchemaBase):
    """删除个人技能同步表（个人技能库 SSOT）参数"""

    pks: list[int] = Field(description='个人技能同步表（个人技能库 SSOT） ID 列表')


class GetMarketplacePersonalSkillDetail(MarketplacePersonalSkillSchemaBase):
    """个人技能同步表（个人技能库 SSOT）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
