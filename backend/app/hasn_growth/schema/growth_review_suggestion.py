from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthReviewSuggestionSchemaBase(SchemaBase):
    """下一周期 ICP、渠道与打法建议及 Owner 审阅结果基础模型"""

    growth_project_id: str | UUID = Field(description='None')
    suggestion_kind: str = Field(description='None')
    proposal: dict = Field(description='None')
    evidence: dict = Field(description='建议证据范围、样本量、数据不足和局限，禁止保存联系人明文')
    proposed_by_kind: str = Field(description='None')
    proposed_by_id: str = Field(description='None')
    idempotency_key: str = Field(description='None')
    status: str = Field(description='None')
    applied_version: int | None = Field(None, description='None')
    reviewed_by_owner_id: str | None = Field(None, description='None')
    reviewed_time: datetime | None = Field(None, description='None')


class CreateGrowthReviewSuggestionParam(GrowthReviewSuggestionSchemaBase):
    """创建下一周期 ICP、渠道与打法建议及 Owner 审阅结果参数"""


class UpdateGrowthReviewSuggestionParam(GrowthReviewSuggestionSchemaBase):
    """更新下一周期 ICP、渠道与打法建议及 Owner 审阅结果参数"""


class DeleteGrowthReviewSuggestionParam(SchemaBase):
    """删除下一周期 ICP、渠道与打法建议及 Owner 审阅结果参数"""

    pks: list[int] = Field(description='下一周期 ICP、渠道与打法建议及 Owner 审阅结果 ID 列表')


class GetGrowthReviewSuggestionDetail(GrowthReviewSuggestionSchemaBase):
    """下一周期 ICP、渠道与打法建议及 Owner 审阅结果详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
