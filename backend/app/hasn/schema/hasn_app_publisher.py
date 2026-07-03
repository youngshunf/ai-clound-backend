from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAppPublisherSchemaBase(SchemaBase):
    """AI-Native 应用发行方（所有权绑定）基础模型"""
    app_id: str = Field(description='应用 ID (全局唯一)')
    developer_id: str = Field(description='开发者 hasn_id (列宽对齐 varchar(40))')
    publisher_type: str = Field(description='发行方类型 (first_party:官方:blue/third_party:第三方:purple)')
    status: str = Field(description='状态 (active:正常:green/suspended:暂停:orange/revoked:吊销:gray)')


class CreateHasnAppPublisherParam(HasnAppPublisherSchemaBase):
    """创建AI-Native 应用发行方（所有权绑定）参数"""


class UpdateHasnAppPublisherParam(HasnAppPublisherSchemaBase):
    """更新AI-Native 应用发行方（所有权绑定）参数"""


class DeleteHasnAppPublisherParam(SchemaBase):
    """删除AI-Native 应用发行方（所有权绑定）参数"""

    pks: list[int] = Field(description='AI-Native 应用发行方（所有权绑定） ID 列表')


class GetHasnAppPublisherDetail(HasnAppPublisherSchemaBase):
    """AI-Native 应用发行方（所有权绑定）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
