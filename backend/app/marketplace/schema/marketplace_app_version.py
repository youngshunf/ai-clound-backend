from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceAppVersionSchemaBase(SchemaBase):
    """应用版本基础模型"""
    app_id: str = Field(description='关联的应用ID')
    version: str = Field(description='语义化版本号')
    changelog: str | None = Field(None, description='版本更新日志')
    skill_dependencies_versioned: dict | None = Field(None, description='带版本号的技能依赖')
    package_url: str | None = Field(None, description='完整包下载URL')
    file_hash: str | None = Field(None, description='SHA256校验值')
    file_size: int | None = Field(None, description='包大小（字节）')
    is_latest: bool = Field(description='是否为最新版本')
    published_at: datetime = Field(description='发布时间')


class CreateMarketplaceAppVersionParam(MarketplaceAppVersionSchemaBase):
    """创建应用版本参数"""


class UpdateMarketplaceAppVersionParam(MarketplaceAppVersionSchemaBase):
    """更新应用版本参数"""


class DeleteMarketplaceAppVersionParam(SchemaBase):
    """删除应用版本参数"""

    pks: list[int] = Field(description='应用版本 ID 列表')


class GetMarketplaceAppVersionDetail(MarketplaceAppVersionSchemaBase):
    """应用版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
