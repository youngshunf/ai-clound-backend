from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ConsumerLinkSchemaBase(SchemaBase):
    """设计系统下游消费登记（换系统重渲染追踪）基础模型"""
    design_system_id: int = Field(description='所属 design_system.id')
    consumer_app: str = Field(description='消费方 (deck:演示文稿:violet/publish:网站发布:blue/creator:创作:cyan)')
    consumer_ref: str = Field(description='消费方资源 id（如 deck_id）')
    bound_revision_id: int | None = Field(None, description='绑定的 revision.id（消费的具体版本，可空）')


class CreateConsumerLinkParam(ConsumerLinkSchemaBase):
    """创建设计系统下游消费登记（换系统重渲染追踪）参数"""


class UpdateConsumerLinkParam(ConsumerLinkSchemaBase):
    """更新设计系统下游消费登记（换系统重渲染追踪）参数"""


class DeleteConsumerLinkParam(SchemaBase):
    """删除设计系统下游消费登记（换系统重渲染追踪）参数"""

    pks: list[int] = Field(description='设计系统下游消费登记（换系统重渲染追踪） ID 列表')


class GetConsumerLinkDetail(ConsumerLinkSchemaBase):
    """设计系统下游消费登记（换系统重渲染追踪）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
