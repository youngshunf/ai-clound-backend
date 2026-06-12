from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadSourceConfigSchemaBase(SchemaBase):
    """AI lead automation source configuration基础模型"""
    source_type: str = Field(description='None')
    name: str = Field(description='None')
    enabled: bool = Field(description='None')
    firecrawl_options: dict = Field(description='None')
    min_contact_fields: dict = Field(description='None')
    persist_raw_html: bool = Field(description='None')
    max_html_bytes: int = Field(description='None')
    domain_blacklist: dict = Field(description='None')
    country_blacklist: dict = Field(description='None')
    rate_limit_per_minute: int = Field(description='None')
    concurrency: int = Field(description='None')
    meta_data: dict = Field(description='None')


class CreateLeadSourceConfigParam(LeadSourceConfigSchemaBase):
    """创建AI lead automation source configuration参数"""


class UpdateLeadSourceConfigParam(LeadSourceConfigSchemaBase):
    """更新AI lead automation source configuration参数"""


class DeleteLeadSourceConfigParam(SchemaBase):
    """删除AI lead automation source configuration参数"""

    pks: list[int] = Field(description='AI lead automation source configuration ID 列表')


class GetLeadSourceConfigDetail(LeadSourceConfigSchemaBase):
    """AI lead automation source configuration详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
