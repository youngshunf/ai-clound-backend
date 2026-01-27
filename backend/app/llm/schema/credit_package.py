from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CreditPackageSchemaBase(SchemaBase):
    """积分包配置表 - 定义可购买的积分包基础模型"""
    package_name: str = Field(description='积分包名称')
    credits: Decimal = Field(description='基础积分数量')
    price: Decimal = Field(description='价格 (USD)')
    bonus_credits: Decimal = Field(description='额外赠送积分')
    description: str | None = Field(None, description='描述')
    enabled: bool = Field(description='是否启用')
    sort_order: int = Field(description='排序权重')


class CreateCreditPackageParam(CreditPackageSchemaBase):
    """创建积分包配置表 - 定义可购买的积分包参数"""


class UpdateCreditPackageParam(CreditPackageSchemaBase):
    """更新积分包配置表 - 定义可购买的积分包参数"""


class DeleteCreditPackageParam(SchemaBase):
    """删除积分包配置表 - 定义可购买的积分包参数"""

    pks: list[int] = Field(description='积分包配置表 - 定义可购买的积分包 ID 列表')


class GetCreditPackageDetail(CreditPackageSchemaBase):
    """积分包配置表 - 定义可购买的积分包详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
