from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSessionEventsSchemaBase(SchemaBase):
    """HASN 会话事件基础模型"""
    session_id: str = Field(description='会话 ID')
    event_type: str = Field(description='事件类型 (session.created/session.paused/task.started/tool.called)')
    event_seq: int = Field(description='会话内事件序号')
    payload_json: str | None = Field(None, description='事件载荷 (JSON)')
    occurred_at: datetime = Field(description='事件发生时间')


class CreateHasnSessionEventsParam(HasnSessionEventsSchemaBase):
    """创建HASN 会话事件参数"""


class UpdateHasnSessionEventsParam(HasnSessionEventsSchemaBase):
    """更新HASN 会话事件参数"""


class DeleteHasnSessionEventsParam(SchemaBase):
    """删除HASN 会话事件参数"""

    pks: list[int] = Field(description='HASN 会话事件 ID 列表')


class GetHasnSessionEventsDetail(HasnSessionEventsSchemaBase):
    """HASN 会话事件详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
