from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceDownloadSchemaBase(SchemaBase):
    """用户下载记录基础模型"""
    user_id: int = Field(description='用户ID')
    resource_type: str = Field(description='资源类型 (skill:技能:blue/template:模板:cyan)')
    resource_id: str = Field(description='资源 ID')
    resource_name: str | None = Field(None, description='资源名称')
    version: str = Field(description='下载的版本')
    download_source: str | None = Field(None, description='下载来源（web/api/cli）')
    ip_address: str | None = Field(None, description='IP 地址')
    user_agent: str | None = Field(None, description='User Agent')


class CreateMarketplaceDownloadParam(SchemaBase):
    """创建用户下载记录参数"""
    user_id: int = Field(description='用户ID')
    resource_type: str = Field(description='资源类型 (skill:技能:blue/template:模板:cyan)')
    resource_id: str = Field(description='资源 ID')
    resource_name: str | None = Field(None, description='资源名称')
    version: str = Field(description='下载的版本')
    download_source: str | None = Field(None, description='下载来源（web/api/cli）')
    ip_address: str | None = Field(None, description='IP 地址')
    user_agent: str | None = Field(None, description='User Agent')


class UpdateMarketplaceDownloadParam(MarketplaceDownloadSchemaBase):
    """更新用户下载记录参数"""


class DeleteMarketplaceDownloadParam(SchemaBase):
    """删除用户下载记录参数"""

    pks: list[int] = Field(description='用户下载记录 ID 列表')


class GetMarketplaceDownloadDetail(MarketplaceDownloadSchemaBase):
    """用户下载记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    downloaded_at: datetime
    created_time: datetime
    updated_time: datetime | None = None
