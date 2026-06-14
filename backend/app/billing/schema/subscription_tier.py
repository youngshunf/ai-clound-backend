from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class SubscriptionTierSchemaBase(SchemaBase):
    """订阅等级配置基础模型"""
    app_code: str = 'huanxing'
    tier_name: str = Field(description='等级标识 (free:免费版/basic:基础版/pro:专业版/enterprise:企业版)')
    display_name: str = Field(description='显示名称')
    monthly_credits: Decimal = Field(description='每月赠送积分')
    monthly_price: Decimal = Field(description='月费')
    yearly_price: Decimal | None = Field(default=None, description='年费')
    yearly_discount: Decimal | None = Field(default=None, description='年费折扣 (如 0.8 表示8折)')
    features: dict = Field(description='功能特性')
    enabled: bool = Field(description='是否启用')
    sort_order: int = Field(description='排序权重')


class CreateSubscriptionTierParam(SubscriptionTierSchemaBase):
    """创建订阅等级配置参数"""


class UpdateSubscriptionTierParam(SubscriptionTierSchemaBase):
    """更新订阅等级配置参数"""


class DeleteSubscriptionTierParam(SchemaBase):
    """删除订阅等级配置参数"""

    pks: list[int] = Field(description='订阅等级配置 ID 列表')


class GetSubscriptionTierDetail(SubscriptionTierSchemaBase):
    """订阅等级配置详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
