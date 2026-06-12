from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadCollectionJobSchemaBase(SchemaBase):
    """AI lead automation collection job基础模型"""
    job_no: str = Field(description='None')
    keyword: str = Field(description='None')
    source_types: dict = Field(description='None')
    lead_scope: str = Field(description='None')
    user_id: int | None = Field(None, description='None')
    status: str = Field(description='None')
    max_pages: int = Field(description='None')
    max_results: int = Field(description='None')
    request_config: dict = Field(description='None')
    total_found: int = Field(description='None')
    raw_count: int = Field(description='None')
    valid_count: int = Field(description='None')
    invalid_count: int = Field(description='None')
    duplicate_count: int = Field(description='None')
    firecrawl_success_count: int = Field(description='None')
    firecrawl_failed_count: int = Field(description='None')
    started_at: datetime | None = Field(None, description='None')
    finished_at: datetime | None = Field(None, description='None')
    error_message: str | None = Field(None, description='None')
    meta_data: dict = Field(description='None')


class CreateLeadCollectionJobParam(LeadCollectionJobSchemaBase):
    """创建AI lead automation collection job参数"""


class UpdateLeadCollectionJobParam(LeadCollectionJobSchemaBase):
    """更新AI lead automation collection job参数"""


class DeleteLeadCollectionJobParam(SchemaBase):
    """删除AI lead automation collection job参数"""

    pks: list[int] = Field(description='AI lead automation collection job ID 列表')


class GetLeadCollectionJobDetail(LeadCollectionJobSchemaBase):
    """AI lead automation collection job详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
