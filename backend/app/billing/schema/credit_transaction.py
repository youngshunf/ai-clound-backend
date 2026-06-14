from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CreditTransactionSchemaBase(SchemaBase):
    """积分交易记录基础模型"""
    app_code: str = 'huanxing'
    user_id: int = Field(description='用户 ID')
    transaction_type: str = Field(description='交易类型 (usage:使用/purchase:购买/refund:退款/monthly_grant:月度赠送/bonus:奖励)')
    credits: Decimal = Field(description='积分变动数量')
    balance_before: Decimal = Field(description='交易前余额')
    balance_after: Decimal = Field(description='交易后余额')
    reference_id: str | None = Field(None, description='关联 ID')
    reference_type: str | None = Field(None, description='关联类型 (llm_usage:LLM调用/payment:支付/system:系统)')
    description: str | None = Field(None, description='交易描述')
    extra_data: dict | None = Field(None, description='扩展数据')


class CreateCreditTransactionParam(CreditTransactionSchemaBase):
    """创建积分交易记录参数"""


class UpdateCreditTransactionParam(CreditTransactionSchemaBase):
    """更新积分交易记录参数"""


class DeleteCreditTransactionParam(SchemaBase):
    """删除积分交易记录参数"""

    pks: list[int] = Field(description='积分交易记录 ID 列表')


class GetCreditTransactionDetail(CreditTransactionSchemaBase):
    """积分交易记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_nickname: str | None = Field(None, description='用户昵称')
    user_phone: str | None = Field(None, description='用户手机号')
    created_time: datetime
    updated_time: datetime | None = None
