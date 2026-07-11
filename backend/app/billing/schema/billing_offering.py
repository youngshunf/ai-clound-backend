from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class BillingOfferingSchemaBase(SchemaBase):
    """商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）基础模型"""
    key: str = Field(description='商品业务键（全端稳定，如 app:quant / llm:tier / webapp:hosting）')
    kind: str = Field(description='商品种类 (llm_tier:LLM订阅档:blue/credit_pack:积分包:cyan/app:应用:green/seat:企业席位:purple/feature_plan:功能档位:orange)')
    feature_key: str = Field(description='付费墙特征键（付费墙通用语言，如 app:<id> / llm:tier / webapp:hosting；集中注册表 feature_registry 校验）')
    display_name: str = Field(description='显示名称')
    status: str = Field(description='状态 (active:上架:green/inactive:下架:gray)')
    source: str = Field(description='商品来源（预留分成维度，platform:平台自营）')
    sort_order: int = Field(description='排序权重')


class CreateBillingOfferingParam(BillingOfferingSchemaBase):
    """创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数"""


class UpdateBillingOfferingParam(BillingOfferingSchemaBase):
    """更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数"""


class DeleteBillingOfferingParam(SchemaBase):
    """删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数"""

    pks: list[int] = Field(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID 列表')


class GetBillingOfferingDetail(BillingOfferingSchemaBase):
    """商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
