from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadFirecrawlRequestSchemaBase(SchemaBase):
    """Firecrawl request audit for AI lead automation基础模型"""
    job_id: int = Field(description='None')
    source_config_id: int | None = Field(None, description='None')
    source_type: str = Field(description='None')
    endpoint: str = Field(description='None')
    target_url: str | None = Field(None, description='None')
    query_data: str | None = Field(None, description='None')
    request_payload: dict = Field(description='None')
    extract_mode: str = Field(description='None')
    llm_schema_version: str | None = Field(None, description='None')
    llm_prompt_version: str | None = Field(None, description='None')
    response_status: int | None = Field(None, description='None')
    status: str = Field(description='None')
    attempt_count: int = Field(description='None')
    duration_ms: int | None = Field(None, description='None')
    result_count: int | None = Field(None, description='None')
    error_message: str | None = Field(None, description='None')
    response_excerpt: str | None = Field(None, description='None')
    meta_data: dict = Field(description='None')


class CreateLeadFirecrawlRequestParam(LeadFirecrawlRequestSchemaBase):
    """创建Firecrawl request audit for AI lead automation参数"""


class UpdateLeadFirecrawlRequestParam(LeadFirecrawlRequestSchemaBase):
    """更新Firecrawl request audit for AI lead automation参数"""


class DeleteLeadFirecrawlRequestParam(SchemaBase):
    """删除Firecrawl request audit for AI lead automation参数"""

    pks: list[int] = Field(description='Firecrawl request audit for AI lead automation ID 列表')


class GetLeadFirecrawlRequestDetail(LeadFirecrawlRequestSchemaBase):
    """Firecrawl request audit for AI lead automation详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
