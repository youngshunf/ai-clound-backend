from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadContactSourceSchemaBase(SchemaBase):
    """Lead multi-source evidence基础模型"""
    lead_contact_id: int = Field(description='None')
    raw_record_id: int | None = Field(None, description='None')
    firecrawl_request_id: int | None = Field(None, description='None')
    source_type: str = Field(description='None')
    source_url: str | None = Field(None, description='None')
    match_dimension: str = Field(description='None')
    seen_at: datetime = Field(description='None')
    meta_data: dict = Field(description='None')


class CreateLeadContactSourceParam(LeadContactSourceSchemaBase):
    """创建Lead multi-source evidence参数"""


class UpdateLeadContactSourceParam(LeadContactSourceSchemaBase):
    """更新Lead multi-source evidence参数"""


class DeleteLeadContactSourceParam(SchemaBase):
    """删除Lead multi-source evidence参数"""

    pks: list[int] = Field(description='Lead multi-source evidence ID 列表')


class GetLeadContactSourceDetail(LeadContactSourceSchemaBase):
    """Lead multi-source evidence详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
