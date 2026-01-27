from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CreditTransactionSchemaBase(SchemaBase):
    """积分交易记录表 - 审计所有积分变动基础模型"""
    user_id: int = Field(description='用户 ID')
    transaction_type: str = Field(description='交易类型: usage/purchase/refund/monthly_grant/bonus/adjustment')
    credits: Decimal = Field(description='积分变动数量 (正数=增加, 负数=扣除)')
    balance_before: Decimal = Field(description='交易前余额')
    balance_after: Decimal = Field(description='交易后余额')
    reference_id: str | None = Field(None, description='关联 ID (如 usage_log.request_id, payment.order_id)')
    reference_type: str | None = Field(None, description='关联类型: llm_usage/payment/system/manual')
    description: str | None = Field(None, description='交易描述')
    metadata: dict | None = Field(None, description='扩展元数据 (JSON)')


class CreateCreditTransactionParam(CreditTransactionSchemaBase):
    """创建积分交易记录表 - 审计所有积分变动参数"""


class UpdateCreditTransactionParam(CreditTransactionSchemaBase):
    """更新积分交易记录表 - 审计所有积分变动参数"""


class DeleteCreditTransactionParam(SchemaBase):
    """删除积分交易记录表 - 审计所有积分变动参数"""

    pks: list[int] = Field(description='积分交易记录表 - 审计所有积分变动 ID 列表')


class GetCreditTransactionDetail(CreditTransactionSchemaBase):
    """积分交易记录表 - 审计所有积分变动详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
