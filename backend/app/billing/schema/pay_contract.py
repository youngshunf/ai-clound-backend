from datetime import date, datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GetPayContractDetail(SchemaBase):
    """签约记录详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    channel_code: str
    contract_no: str
    channel_contract_id: str | None = None
    plan_id: str | None = None
    tier: str
    billing_cycle: str
    deduct_amount: int
    status: int = Field(description='0=签约中 1=已签约 2=已解约 3=签约失败')
    signed_time: datetime | None = None
    terminated_time: datetime | None = None
    terminate_reason: str | None = None
    next_deduct_date: date | None = None
    last_deduct_time: datetime | None = None
    deduct_count: int
    extra_data: dict | None = None
    created_time: datetime
    updated_time: datetime | None = None


class GetPayContractUserView(SchemaBase):
    """用户端签约状态"""
    has_contract: bool = Field(description='是否有有效签约')
    tier: str | None = Field(None, description='签约套餐')
    billing_cycle: str | None = Field(None, description='计费周期')
    deduct_amount: int | None = Field(None, description='每期扣款金额（分）')
    next_deduct_date: date | None = Field(None, description='下次扣款日期')
    channel_code: str | None = Field(None, description='签约渠道')
