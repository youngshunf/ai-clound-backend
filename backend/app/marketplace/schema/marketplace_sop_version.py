from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceSopVersionSchemaBase(SchemaBase):
    """SOP版本基础模型"""
    sop_id: str = Field(description='关联的SOP ID')
    version: str = Field(description='语义化版本号')
    changelog: str | None = Field(None, description='版本更新日志')
    package_url: str | None = Field(None, description='完整包下载URL')
    file_hash: str | None = Field(None, description='SHA256校验值')
    file_size: int | None = Field(None, description='包大小（字节）')
    is_latest: bool = Field(description='是否为最新版本')
    published_at: datetime = Field(description='发布时间')


class CreateMarketplaceSopVersionParam(MarketplaceSopVersionSchemaBase):
    """创建SOP版本参数"""


class UpdateMarketplaceSopVersionParam(MarketplaceSopVersionSchemaBase):
    """更新SOP版本参数"""


class DeleteMarketplaceSopVersionParam(SchemaBase):
    """删除SOP版本参数"""

    pks: list[int] = Field(description='SOP版本 ID 列表')


class GetMarketplaceSopVersionDetail(MarketplaceSopVersionSchemaBase):
    """SOP版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
