from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceDownloadSchemaBase(SchemaBase):
    """用户下载记录基础模型"""
    user_id: int = Field(description='用户ID')
    item_type: str = Field(description='类型 (app:应用/skill:技能)')
    item_id: str = Field(description='应用或技能ID')
    version: str = Field(description='下载的版本')


class CreateMarketplaceDownloadParam(SchemaBase):
    """创建用户下载记录参数"""
    user_id: int = Field(description='用户ID')
    item_type: str = Field(description='类型 (app:应用/skill:技能)')
    item_id: str = Field(description='应用或技能ID')
    version: str = Field(description='下载的版本')


class UpdateMarketplaceDownloadParam(MarketplaceDownloadSchemaBase):
    """更新用户下载记录参数"""


class DeleteMarketplaceDownloadParam(SchemaBase):
    """删除用户下载记录参数"""

    pks: list[int] = Field(description='用户下载记录 ID 列表')


class GetMarketplaceDownloadDetail(MarketplaceDownloadSchemaBase):
    """用户下载记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
