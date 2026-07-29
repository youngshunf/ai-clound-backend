from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OpportunitySchemaBase(SchemaBase):
    """获客商机（阶段推进 + 金额 + 成交/败因登记）基础模型"""
    opportunity_no: str = Field(description='None')
    customer_id: int = Field(description='None')
    user_id: int = Field(description='None')
    name: str = Field(description='None')
    version: int = Field(default=1, ge=1, description='并发控制版本')
    stage: str = Field(description='阶段 (contacted:已触达:blue/replied:已回应:cyan/proposal:已发提案:purple/negotiation:商务洽谈:orange/closed_won:成交:green/closed_lost:流失:red)')
    amount: Decimal | None = Field(None, description='None')
    currency: str = Field(description='None')
    probability: Decimal | None = Field(None, description='None')
    expected_close_at: datetime | None = Field(None, description='None')
    won_at: datetime | None = Field(None, description='None')
    lost_at: datetime | None = Field(None, description='None')
    lost_reason: str | None = Field(None, description='None')
    close_note: str | None = Field(None, description='None')
    review_task_id: str | None = Field(None, description='复盘任务 UUID')
    created_by_kind: str = Field(description='创建者 (owner:主人:blue/agent:分身:violet)')


class CreateOpportunityParam(OpportunitySchemaBase):
    """创建获客商机（阶段推进 + 金额 + 成交/败因登记）参数"""


class UpdateOpportunityParam(OpportunitySchemaBase):
    """更新获客商机（阶段推进 + 金额 + 成交/败因登记）参数"""


class DeleteOpportunityParam(SchemaBase):
    """删除获客商机（阶段推进 + 金额 + 成交/败因登记）参数"""

    pks: list[int] = Field(description='获客商机（阶段推进 + 金额 + 成交/败因登记） ID 列表')


class GetOpportunityDetail(OpportunitySchemaBase):
    """获客商机（阶段推进 + 金额 + 成交/败因登记）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
