from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ModelCreditRateSchemaBase(SchemaBase):
    """模型积分费率表 - 定义不同模型的积分消耗规则基础模型"""
    model_id: int = Field(description='模型 ID (外键 llm_model_config.id)')
    base_credit_per_1k_tokens: Decimal = Field(description='基准积分费率 (每 1K tokens)')
    input_multiplier: Decimal = Field(description='输入 tokens 倍率')
    output_multiplier: Decimal = Field(description='输出 tokens 倍率')
    enabled: bool = Field(description='是否启用')


class CreateModelCreditRateParam(ModelCreditRateSchemaBase):
    """创建模型积分费率表 - 定义不同模型的积分消耗规则参数"""


class UpdateModelCreditRateParam(ModelCreditRateSchemaBase):
    """更新模型积分费率表 - 定义不同模型的积分消耗规则参数"""


class DeleteModelCreditRateParam(SchemaBase):
    """删除模型积分费率表 - 定义不同模型的积分消耗规则参数"""

    pks: list[int] = Field(description='模型积分费率表 - 定义不同模型的积分消耗规则 ID 列表')


class GetModelCreditRateDetail(ModelCreditRateSchemaBase):
    """模型积分费率表 - 定义不同模型的积分消耗规则详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
