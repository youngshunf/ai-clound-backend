from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProjectLeadSchemaBase(SchemaBase):
    """获客漏斗对全局联系人事实的项目级引用基础模型"""

    growth_project_id: UUID = Field(description='None')
    lead_contact_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    source_kind: str | None = Field(None, description='None')
    source_tool: str | None = Field(None, description='None')
    source_ref: str | None = Field(None, description='None')
    source_meta: dict = Field(description='None')
    status: str = Field(description='None')
    dismiss_reason: str | None = Field(None, description='None')
    note: str | None = Field(None, description='None')
    match_score: Decimal | None = Field(None, description='None')
    score_breakdown: dict = Field(description='None')
    scoring_version: str | None = Field(None, description='None')
    evidence_fresh_at: datetime | None = Field(None, description='None')
    acquired_at: datetime = Field(description='None')


class CreateGrowthProjectLeadParam(GrowthProjectLeadSchemaBase):
    """创建获客漏斗对全局联系人事实的项目级引用参数"""


class UpdateGrowthProjectLeadParam(GrowthProjectLeadSchemaBase):
    """更新获客漏斗对全局联系人事实的项目级引用参数"""


class DeleteGrowthProjectLeadParam(SchemaBase):
    """删除获客漏斗对全局联系人事实的项目级引用参数"""

    pks: list[int] = Field(description='获客漏斗对全局联系人事实的项目级引用 ID 列表')


class GetGrowthProjectLeadDetail(GrowthProjectLeadSchemaBase):
    """获客漏斗对全局联系人事实的项目级引用详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
