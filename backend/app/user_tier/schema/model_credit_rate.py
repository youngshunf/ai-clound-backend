from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ModelCreditRateSchemaBase(SchemaBase):
    """模型积分费率基础模型"""
    model_id: int = Field(description='模型 ID')
    base_credit_per_1k_tokens: Decimal = Field(description='基准积分费率')
    input_multiplier: Decimal = Field(description='输入倍率')
    output_multiplier: Decimal = Field(description='输出倍率')
    enabled: bool = Field(description='是否启用')


class CreateModelCreditRateParam(ModelCreditRateSchemaBase):
    """创建模型积分费率参数"""


class UpdateModelCreditRateParam(ModelCreditRateSchemaBase):
    """更新模型积分费率参数"""


class DeleteModelCreditRateParam(SchemaBase):
    """删除模型积分费率参数"""

    pks: list[int] = Field(description='模型积分费率 ID 列表')


class GetModelCreditRateDetail(ModelCreditRateSchemaBase):
    """模型积分费率详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    model_name: str | None = Field(default=None, description='模型名称')
    provider_name: str | None = Field(default=None, description='供应商名称')
    created_time: datetime
    updated_time: datetime | None = None
