from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnNotificationImCommandOutboxSchemaBase(SchemaBase):
    """通知业务状态触发 IM 卡片的事务命令队列基础模型"""
    command_id: str = Field(description='命令公开标识')
    producer: str = Field(description='生产方固定标识 notification')
    conversation_id: str | UUID = Field(description='ensure 后取得的权威会话 ID')
    command_type: str = Field(description='命令类型，当前仅 send_message')
    payload: dict = Field(description='版本化的认证主体与发送命令 JSON')
    payload_hash: str = Field(description='规范化命令载荷 SHA-256，用于同键异载荷冲突检测')
    idempotency_key: str = Field(description='跨 relay 重试稳定的 IM 幂等键')
    status: str = Field(description='投递状态：pending/processing/completed/dead_letter')
    attempt_count: int = Field(description='已失败次数')
    next_attempt_at: datetime = Field(description='下次允许领取时间')
    lease_until: datetime | None = Field(None, description='处理中租约截止时间')
    locked_by: str | None = Field(None, description='当前 relay 实例标识')
    last_error: str | None = Field(None, description='最近一次失败诊断')
    message_id: int | None = Field(None, description='成功投递后的权威消息 ID')
    trace_id: str | None = Field(None, description='跨服务追踪标识')
    causation_id: str | None = Field(None, description='触发本命令的业务事实标识')
    completed_at: datetime | None = Field(None, description='投递完成时间')


class CreateHasnNotificationImCommandOutboxParam(HasnNotificationImCommandOutboxSchemaBase):
    """创建通知业务状态触发 IM 卡片的事务命令队列参数"""


class UpdateHasnNotificationImCommandOutboxParam(HasnNotificationImCommandOutboxSchemaBase):
    """更新通知业务状态触发 IM 卡片的事务命令队列参数"""


class DeleteHasnNotificationImCommandOutboxParam(SchemaBase):
    """删除通知业务状态触发 IM 卡片的事务命令队列参数"""

    pks: list[int] = Field(description='通知业务状态触发 IM 卡片的事务命令队列 ID 列表')


class GetHasnNotificationImCommandOutboxDetail(HasnNotificationImCommandOutboxSchemaBase):
    """通知业务状态触发 IM 卡片的事务命令队列详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
