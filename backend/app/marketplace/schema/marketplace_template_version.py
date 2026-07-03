from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceTemplateVersionSchemaBase(SchemaBase):
    """模板版本基础模型"""
    template_id: str = Field(description='关联的模板ID')
    version: str = Field(description='语义化版本号')
    changelog: str | None = Field(None, description='版本更新日志')
    skill_dependencies_versioned: dict | None = Field(None, description='带版本号的技能依赖')
    bundle_slug: str | None = Field(None, description='skill pack slug')
    command_key: str | None = Field(None, description='Hermes 命令 key')
    hermes_bundle_json: dict | None = Field(None, description='Hermes bundle JSON')
    hermes_yaml: str | None = Field(None, description='Hermes YAML')
    content_hash: str | None = Field(None, description='内容哈希')
    package_url: str | None = Field(None, description='完整包下载URL')
    file_hash: str | None = Field(None, description='SHA256校验值')
    file_size: int | None = Field(None, description='包大小（字节）')
    is_latest: bool = Field(description='是否为最新版本')
    published_at: datetime = Field(description='发布时间')


class CreateMarketplaceTemplateVersionParam(MarketplaceTemplateVersionSchemaBase):
    """创建模板版本参数"""


class UpdateMarketplaceTemplateVersionParam(MarketplaceTemplateVersionSchemaBase):
    """更新模板版本参数"""


class DeleteMarketplaceTemplateVersionParam(SchemaBase):
    """删除模板版本参数"""

    pks: list[int] = Field(description='模板版本 ID 列表')


class GetMarketplaceTemplateVersionDetail(MarketplaceTemplateVersionSchemaBase):
    """模板版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
