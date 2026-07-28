from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OutreachMessageEventSchemaBase(SchemaBase):
    """触达审批、投递、拦截、人工证明和回复的追加式事件基础模型"""

    growth_project_id: UUID = Field(description='None')
    outreach_message_id: int = Field(description='None')
    event_type: str = Field(description='None')
    idempotency_key: str = Field(description='None')
    occurred_time: datetime = Field(description='None')
    actor_kind: str = Field(description='None')
    actor_id: str | None = Field(None, description='None')
    approval_status: str | None = Field(None, description='None')
    delivery_status: str | None = Field(None, description='None')
    approval_version: int | None = Field(None, description='None')
    content_version: int | None = Field(None, description='None')
    error_class: str | None = Field(None, description='None')
    meta_data: dict = Field(description='None')


class CreateOutreachMessageEventParam(OutreachMessageEventSchemaBase):
    """创建触达审批、投递、拦截、人工证明和回复的追加式事件参数"""


class UpdateOutreachMessageEventParam(OutreachMessageEventSchemaBase):
    """更新触达审批、投递、拦截、人工证明和回复的追加式事件参数"""


class DeleteOutreachMessageEventParam(SchemaBase):
    """删除触达审批、投递、拦截、人工证明和回复的追加式事件参数"""

    pks: list[int] = Field(description='触达审批、投递、拦截、人工证明和回复的追加式事件 ID 列表')


class GetOutreachMessageEventDetail(OutreachMessageEventSchemaBase):
    """触达审批、投递、拦截、人工证明和回复的追加式事件详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
