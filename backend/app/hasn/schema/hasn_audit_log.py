from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAuditLogSchemaBase(SchemaBase):
    """HASN 审计日志基础模型"""
    actor_id: str = Field(description='操作者 hasn_id')
    actor_type: str = Field(description='操作者类型 (human:人类:blue/agent:代理:green/system:系统:gray)')
    action: str = Field(description='操作类型 (register:注册:blue/login:登录:green/send_message:发消息:cyan/add_contact:加好友:orange/block_contact:拉黑:red/create_agent:创建Agent:purple/delete_agent:删除Agent:red/bind_client:绑定客户端:green/unbind_client:解绑客户端:orange)')
    target_type: str | None = Field(None, description='目标类型 (human:人类:blue/agent:代理:green/client:客户端:orange/conversation:会话:cyan/message:消息:purple)')
    target_id: str | None = Field(None, description='目标 ID')
    details: dict = Field(description='操作详情 (JSONB)')
    ip_address: str | None = Field(None, description='IP 地址')


class CreateHasnAuditLogParam(HasnAuditLogSchemaBase):
    """创建HASN 审计日志参数"""


class UpdateHasnAuditLogParam(HasnAuditLogSchemaBase):
    """更新HASN 审计日志参数"""


class DeleteHasnAuditLogParam(SchemaBase):
    """删除HASN 审计日志参数"""

    pks: list[int] = Field(description='HASN 审计日志 ID 列表')


class GetHasnAuditLogDetail(HasnAuditLogSchemaBase):
    """HASN 审计日志详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
