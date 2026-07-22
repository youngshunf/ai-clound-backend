from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnMessagesSchemaBase(SchemaBase):
    """HASN 消息基础模型"""
    conversation_id: str | UUID = Field(description='所属会话 ID')
    from_id: str = Field(description='发送方 hasn_id')
    from_type: int = Field(description='发送方类型 (1:人类:blue/2:代理:green/3:系统:gray)')
    to_id: str = Field(description='接收方标识（单聊=hasn_id，群聊=group_id 如 g:500001）')
    to_type: int = Field(description='接收方类型 (1:人类:blue/2:代理:green/3:系统:gray/4:群组:purple)')
    content_type: int = Field(description='内容类型 (1:文本:blue/2:图片:green/3:文件:orange/4:语音:cyan/5:卡片:purple/6:能力请求:red/7:能力响应:gray)')
    content: dict = Field(description='消息内容 (JSONB)')
    process_blocks: list[dict] = Field(default_factory=list, description='消息生成过程块（JSONB 数组，按产生顺序保存 stream_chunk/tool_call/status 等事件）')
    msg_type: str = Field(description='消息类型 (message:普通消息:blue/contact_request:好友请求:orange/contact_accept:接受好友:green/contact_reject:拒绝好友:red/group_invite:群邀请:purple/group_update:群变更:cyan/notification:通知:cyan/system:系统消息:gray)')
    status: int = Field(description='消息状态 (1:已发送:blue/2:已送达:cyan/3:已读:green/4:已撤回:red)')
    priority: str = Field(description='优先级 (critical:紧急:red/high:高:orange/normal:普通:blue/low:低:gray)')
    reply_to_id: int | None = Field(None, description='回复的消息 ID')
    local_id: str | UUID | None = Field(None, description='客户端本地 ID（UUID, 用于去重）')
    mentions: list[dict[str, Any]] | None = Field(None, description='@提及列表（JSONB: [{hasn_id, star_id, offset, length}]）')
    mention_all: bool = Field(description='是否 @所有人')
    context: dict | None = Field(None, description='消息上下文 (JSONB)')
    recalled_at: datetime | None = Field(None, description='撤回时间')
    recalled_by: str | None = Field(None, description='撤回者 hasn_id')
    edited_at: datetime | None = Field(None, description='最后编辑时间')
    edit_version: int = Field(description='编辑版本号')
    server_received_at: datetime = Field(description='服务端接收时间')


class CreateHasnMessagesParam(HasnMessagesSchemaBase):
    """创建HASN 消息参数"""


class UpdateHasnMessagesParam(HasnMessagesSchemaBase):
    """更新HASN 消息参数"""


class DeleteHasnMessagesParam(SchemaBase):
    """删除HASN 消息参数"""

    pks: list[int] = Field(description='HASN 消息 ID 列表')


class GetHasnMessagesDetail(HasnMessagesSchemaBase):
    """HASN 消息详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
