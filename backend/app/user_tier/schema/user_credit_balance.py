from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class UserCreditBalanceSchemaBase(SchemaBase):
    """用户积分余额基础模型"""

    user_id: int = Field(description='用户 ID')
    credit_type: str = Field(
        description='积分类型 (monthly:月度赠送:blue/purchased:购买积分:green/bonus:活动赠送:orange)'
    )
    original_amount: Decimal = Field(description='原始积分数量')
    used_amount: Decimal = Field(default=Decimal('0'), description='已使用积分')
    remaining_amount: Decimal = Field(description='剩余积分数量')
    expires_at: datetime | None = Field(None, description='过期时间')
    granted_at: datetime = Field(description='发放时间')
    source_type: str = Field(
        description='来源类型 (subscription_grant:订阅发放/subscription_upgrade:升级发放/purchase:购买/bonus:赠送/refund:退款返还)'
    )
    source_reference_id: str | None = Field(None, description='关联订单号')
    description: str | None = Field(None, description='描述')


class CreateUserCreditBalanceParam(UserCreditBalanceSchemaBase):
    """创建用户积分余额参数"""


class UpdateUserCreditBalanceParam(UserCreditBalanceSchemaBase):
    """更新用户积分余额参数"""


class DeleteUserCreditBalanceParam(SchemaBase):
    """删除用户积分余额参数"""

    pks: list[int] = Field(description='用户积分余额 ID 列表')


class GetUserCreditBalanceDetail(UserCreditBalanceSchemaBase):
    """用户积分余额详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
