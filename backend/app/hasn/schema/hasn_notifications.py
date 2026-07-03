from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnNotificationsSchemaBase(SchemaBase):
    """HASN 通知队列基础模型"""
    target_id: str = Field(description='通知目标 hasn_id')
    type: str = Field(description='通知类型 (contact_request:好友请求:blue/contact_accepted:好友接受:green/message_summary:消息摘要:cyan/event_reminder:事件提醒:orange/system:系统通知:gray)')
    title: str = Field(description='通知标题')
    body: str | None = Field(None, description='通知正文')
    data: dict = Field(description='附加数据 (JSONB)')
    read: bool = Field(description='是否已读')


class CreateHasnNotificationsParam(HasnNotificationsSchemaBase):
    """创建HASN 通知队列参数"""


class UpdateHasnNotificationsParam(HasnNotificationsSchemaBase):
    """更新HASN 通知队列参数"""


class DeleteHasnNotificationsParam(SchemaBase):
    """删除HASN 通知队列参数"""

    pks: list[int] = Field(description='HASN 通知队列 ID 列表')


class GetHasnNotificationsDetail(HasnNotificationsSchemaBase):
    """HASN 通知队列详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
