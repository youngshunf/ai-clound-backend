"""P0 HASN S4 message hub schemas.

These models mirror docs/openapi-hasn-cloud-v1.yaml for the S4 endpoints.
Do not add public fields here unless the frozen OpenAPI contract changes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.common.schema import SchemaBase

DispatchStatus = Literal[
    'not_required',
    'pending_runtime',
    'dispatched',
    'runtime_unavailable',
    'dispatch_failed',
    'suppressed_by_policy',
]
InboxKind = Literal['human_inbox', 'agent_inbox', 'owner_copy', 'suppressed_inbox']
DeliveryStatus = Literal['delivered', 'rejected']


class ErrorObject(SchemaBase):
    code: int = Field(description='错误码')
    name: str = Field(description='错误名称')
    message: str = Field(description='错误说明')
    trace_id: str | None = Field(default=None, description='追踪 ID')
    detail: dict[str, Any] | None = Field(default=None, description='附加详情')


class MessageHubSendRequest(SchemaBase):
    owner_id: str = Field(description='发送方/调用方 Owner HASN ID')
    envelope: dict[str, Any] = Field(
        description='HASN HasnEnvelope-compatible payload; canonical schema lives in HASN-Protocol.'
    )
    require_runtime_execution: bool = Field(default=False, description='是否要求 Runtime 自动执行')


class MessageHubSendResponse(SchemaBase):
    message_id: str = Field(description='主消息 ID')
    conversation_id: str = Field(description='会话 ID')
    delivery_status: DeliveryStatus = Field(description='消息入箱状态')
    dispatch_status: DispatchStatus = Field(description='Runtime 调度状态')
    owner_copy_created: bool = Field(default=False, description='是否创建 Owner 可见副本')
    suppressed_inbox_created: bool = Field(default=False, description='是否写入 suppressed inbox')
    warnings: list[ErrorObject] = Field(default_factory=list, description='非阻塞告警')


class InboxPullRequest(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    cursor: str | None = Field(default=None, description='游标')
    include_suppressed: bool = Field(default=True, description='是否包含 suppressed inbox')


class InboxItem(SchemaBase):
    message_id: str = Field(description='消息 ID')
    owner_id: str = Field(description='Owner HASN ID')
    hasn_id: str = Field(description='Inbox 主体 HASN ID')
    conversation_id: str = Field(description='会话 ID')
    inbox_kind: InboxKind = Field(description='Inbox 类型')
    envelope: dict[str, Any] = Field(default_factory=dict, description='HASN Envelope 语义载荷')
    dispatch_status: DispatchStatus = Field(description='Runtime 调度状态')
    created_at: datetime = Field(description='创建时间')


class InboxPullResponse(SchemaBase):
    items: list[InboxItem] = Field(description='Inbox 批次')
    next_cursor: str = Field(description='下一游标')
    has_more: bool = Field(description='是否还有更多')
