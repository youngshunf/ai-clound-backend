from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProfileSuggestionSchemaBase(SchemaBase):
    """分身或系统提出、等待主人确认的画像建议基础模型"""

    growth_project_id: str | UUID = Field(description='None')
    expected_version: int = Field(description='None')
    product_profile: dict = Field(description='None')
    icp_profile: dict = Field(description='None')
    knowledge_document_versions: list[dict] = Field(description='None')
    source_hash: str = Field(description='None')
    proposed_by_kind: str = Field(description='建议主体 (agent:AI分身:purple/system:系统:gray)')
    proposed_by_id: str = Field(description='None')
    trace_id: str | UUID = Field(description='None')
    idempotency_key: str = Field(description='None')
    status: str = Field(
        description=('状态 (pending:待确认:orange/accepted:已接受:green/rejected:已拒绝:red/stale:版本冲突:gray)')
    )
    reviewed_by_owner_id: str | None = Field(None, description='None')
    reviewed_time: datetime | None = Field(None, description='None')


class CreateGrowthProfileSuggestionParam(GrowthProfileSuggestionSchemaBase):
    """创建分身或系统提出、等待主人确认的画像建议参数"""


class UpdateGrowthProfileSuggestionParam(GrowthProfileSuggestionSchemaBase):
    """更新分身或系统提出、等待主人确认的画像建议参数"""


class DeleteGrowthProfileSuggestionParam(SchemaBase):
    """删除分身或系统提出、等待主人确认的画像建议参数"""

    pks: list[int] = Field(description='分身或系统提出、等待主人确认的画像建议 ID 列表')


class GetGrowthProfileSuggestionDetail(GrowthProfileSuggestionSchemaBase):
    """分身或系统提出、等待主人确认的画像建议详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
