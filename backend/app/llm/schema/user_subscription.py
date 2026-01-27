from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class UserSubscriptionSchemaBase(SchemaBase):
    """用户订阅表 - 管理用户的订阅等级和积分余额基础模型"""
    user_id: int = Field(description='用户 ID (外键 sys_user.id)')
    tier: str = Field(description='订阅等级: free/basic/pro/enterprise')
    monthly_credits: Decimal = Field(description='每月积分配额')
    current_credits: Decimal = Field(description='当前剩余积分 (月度积分 + 购买积分)')
    used_credits: Decimal = Field(description='本周期已使用积分')
    purchased_credits: Decimal = Field(description='购买的额外积分 (不会过期)')
    billing_cycle_start: datetime = Field(description='计费周期开始时间 (按用户注册日期)')
    billing_cycle_end: datetime = Field(description='计费周期结束时间 (30天后)')
    status: str = Field(description='订阅状态: active/expired/cancelled')
    auto_renew: bool = Field(description='是否自动续费')


class CreateUserSubscriptionParam(UserSubscriptionSchemaBase):
    """创建用户订阅表 - 管理用户的订阅等级和积分余额参数"""


class UpdateUserSubscriptionParam(UserSubscriptionSchemaBase):
    """更新用户订阅表 - 管理用户的订阅等级和积分余额参数"""


class DeleteUserSubscriptionParam(SchemaBase):
    """删除用户订阅表 - 管理用户的订阅等级和积分余额参数"""

    pks: list[int] = Field(description='用户订阅表 - 管理用户的订阅等级和积分余额 ID 列表')


class GetUserSubscriptionDetail(UserSubscriptionSchemaBase):
    """用户订阅表 - 管理用户的订阅等级和积分余额详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
