from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthAttributionEventSchemaBase(SchemaBase):
    """可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实基础模型"""

    growth_project_id: UUID = Field(description='None')
    event_type: str = Field(description='None')
    lead_contact_id: int | None = Field(None, description='None')
    customer_id: int | None = Field(None, description='None')
    opportunity_id: int | None = Field(None, description='None')
    growth_project_playbook_id: int | None = Field(None, description='None')
    playbook_id: int | None = Field(None, description='None')
    playbook_version: int | None = Field(None, description='None')
    source_kind: str | None = Field(None, description='None')
    source_ref: str | None = Field(None, description='None')
    campaign_ref: str | None = Field(None, description='None')
    playbook_ref: str | None = Field(None, description='None')
    amount: Decimal | None = Field(None, description='None')
    currency: str | None = Field(None, description='None')
    occurred_time: datetime = Field(description='None')
    idempotency_key: str = Field(description='None')
    meta_data: dict = Field(description='None')


class CreateGrowthAttributionEventParam(GrowthAttributionEventSchemaBase):
    """创建可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实参数"""


class UpdateGrowthAttributionEventParam(GrowthAttributionEventSchemaBase):
    """更新可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实参数"""


class DeleteGrowthAttributionEventParam(SchemaBase):
    """删除可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实参数"""

    pks: list[int] = Field(description='可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实 ID 列表')


class GetGrowthAttributionEventDetail(GrowthAttributionEventSchemaBase):
    """可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
