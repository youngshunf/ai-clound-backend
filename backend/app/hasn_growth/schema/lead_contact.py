from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadContactSchemaBase(SchemaBase):
    """Valid deduplicated lead contact基础模型"""
    lead_no: str = Field(description='None')
    lead_scope: str = Field(description='None')
    user_id: int | None = Field(None, description='None')
    company_name: str | None = Field(None, description='None')
    contact_name: str | None = Field(None, description='None')
    email: str | None = Field(None, description='None')
    email_normalized: str | None = Field(None, description='None')
    phone: str | None = Field(None, description='None')
    phone_normalized: str | None = Field(None, description='None')
    website: str | None = Field(None, description='None')
    domain: str | None = Field(None, description='None')
    country: str | None = Field(None, description='None')
    region: str | None = Field(None, description='None')
    city: str | None = Field(None, description='None')
    address: str | None = Field(None, description='None')
    industry: str | None = Field(None, description='None')
    source_type: str | None = Field(None, description='None')
    source_url: str | None = Field(None, description='None')
    keyword: str | None = Field(None, description='None')
    status: str = Field(description='None')
    confidence_score: Decimal = Field(description='None')
    dedupe_key_email: str | None = Field(None, description='None')
    dedupe_key_phone: str | None = Field(None, description='None')
    dedupe_key_domain: str | None = Field(None, description='None')
    normalization_version: str = Field(description='None')
    first_seen_at: datetime = Field(description='None')
    last_seen_at: datetime = Field(description='None')
    last_exported_at: datetime | None = Field(None, description='None')
    archived_at: datetime = Field(description='None')
    meta_data: dict = Field(description='None')


class CreateLeadContactParam(LeadContactSchemaBase):
    """创建Valid deduplicated lead contact参数"""


class UpdateLeadContactParam(LeadContactSchemaBase):
    """更新Valid deduplicated lead contact参数"""


class DeleteLeadContactParam(SchemaBase):
    """删除Valid deduplicated lead contact参数"""

    pks: list[int] = Field(description='Valid deduplicated lead contact ID 列表')


class GetLeadContactDetail(LeadContactSchemaBase):
    """Valid deduplicated lead contact详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
