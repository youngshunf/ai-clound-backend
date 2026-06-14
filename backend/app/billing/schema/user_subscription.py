from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class UserSubscriptionSchemaBase(SchemaBase):
    """用户订阅基础模型"""
    app_code: str = 'huanxing'
    user_id: int = Field(description='用户 ID')
    tier: str = Field(description='订阅等级 (free:免费版/basic:基础版/pro:专业版/enterprise:企业版)')
    subscription_type: str = Field(default='monthly', description='订阅类型 (monthly:月度/yearly:年度)')
    monthly_credits: Decimal = Field(description='每月积分配额')
    current_credits: Decimal = Field(description='当前剩余积分')
    used_credits: Decimal = Field(description='本周期已使用积分')
    purchased_credits: Decimal = Field(description='购买的额外积分')
    billing_cycle_start: datetime = Field(description='计费周期开始时间')
    billing_cycle_end: datetime = Field(description='计费周期结束时间')
    subscription_start_date: datetime | None = Field(default=None, description='订阅开始时间')
    subscription_end_date: datetime | None = Field(default=None, description='订阅结束时间')
    next_grant_date: datetime | None = Field(default=None, description='下次赠送积分时间 (年度订阅专用)')
    status: str = Field(description='订阅状态 (active:激活/expired:已过期/cancelled:已取消)')
    auto_renew: bool = Field(description='是否自动续费')


class CreateUserSubscriptionParam(UserSubscriptionSchemaBase):
    """创建用户订阅参数"""


class UpdateUserSubscriptionParam(UserSubscriptionSchemaBase):
    """更新用户订阅参数"""


class DeleteUserSubscriptionParam(SchemaBase):
    """删除用户订阅参数"""

    pks: list[int] = Field(description='用户订阅 ID 列表')


class GetUserSubscriptionDetail(UserSubscriptionSchemaBase):
    """用户订阅详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_nickname: str | None = Field(None, description='用户昵称')
    user_phone: str | None = Field(None, description='用户手机号')
    created_time: datetime
    updated_time: datetime | None = None
