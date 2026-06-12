from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadRejectedRecordSchemaBase(SchemaBase):
    """Rejected, invalid, duplicate, or failed lead record基础模型"""
    job_id: int = Field(description='None')
    raw_record_id: int | None = Field(None, description='None')
    firecrawl_request_id: int | None = Field(None, description='None')
    source_type: str | None = Field(None, description='None')
    source_url: str | None = Field(None, description='None')
    reason: str = Field(description='None')
    email: str | None = Field(None, description='None')
    phone: str | None = Field(None, description='None')
    raw_excerpt: str | None = Field(None, description='None')
    duplicate_contact_id: int | None = Field(None, description='None')
    error_message: str | None = Field(None, description='None')
    meta_data: dict = Field(description='None')


class CreateLeadRejectedRecordParam(LeadRejectedRecordSchemaBase):
    """创建Rejected, invalid, duplicate, or failed lead record参数"""


class UpdateLeadRejectedRecordParam(LeadRejectedRecordSchemaBase):
    """更新Rejected, invalid, duplicate, or failed lead record参数"""


class DeleteLeadRejectedRecordParam(SchemaBase):
    """删除Rejected, invalid, duplicate, or failed lead record参数"""

    pks: list[int] = Field(description='Rejected, invalid, duplicate, or failed lead record ID 列表')


class GetLeadRejectedRecordDetail(LeadRejectedRecordSchemaBase):
    """Rejected, invalid, duplicate, or failed lead record详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
