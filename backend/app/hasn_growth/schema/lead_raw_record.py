from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadRawRecordSchemaBase(SchemaBase):
    """Raw crawled lead page record基础模型"""
    job_id: int = Field(description='None')
    source_config_id: int | None = Field(None, description='None')
    firecrawl_request_id: int | None = Field(None, description='None')
    source_type: str = Field(description='None')
    source_url: str | None = Field(None, description='None')
    domain: str | None = Field(None, description='None')
    title: str | None = Field(None, description='None')
    markdown: str | None = Field(None, description='None')
    raw_text: str | None = Field(None, description='None')
    raw_html: str | None = Field(None, description='None')
    raw_payload: dict | None = Field(None, description='None')
    structured_payload: dict | None = Field(None, description='None')
    llm_confidence: Decimal | None = Field(None, description='None')
    system_score: Decimal | None = Field(None, description='None')
    content_hash: str = Field(description='None')
    normalization_version: str = Field(description='None')
    status: str = Field(description='None')
    error_message: str | None = Field(None, description='None')
    meta_data: dict = Field(description='None')


class CreateLeadRawRecordParam(LeadRawRecordSchemaBase):
    """创建Raw crawled lead page record参数"""


class UpdateLeadRawRecordParam(LeadRawRecordSchemaBase):
    """更新Raw crawled lead page record参数"""


class DeleteLeadRawRecordParam(SchemaBase):
    """删除Raw crawled lead page record参数"""

    pks: list[int] = Field(description='Raw crawled lead page record ID 列表')


class GetLeadRawRecordDetail(LeadRawRecordSchemaBase):
    """Raw crawled lead page record详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
