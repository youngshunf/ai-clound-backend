from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HermesAgentChannelBindingSchemaBase(SchemaBase):
    """Hermes Agent 渠道绑定基础模型"""
    binding_id: str = Field(description='绑定业务 ID')
    agent_id: str = Field(description='Agent 业务 ID')
    user_id: int = Field(description='用户 ID')
    channel: str = Field(description='渠道 (feishu:飞书:blue/weixin:微信:green/qq:QQ:purple)')
    bind_mode: str = Field(description='绑定方式 (qr:扫码:green/manual:手动:blue/webhook:回调:orange)')
    status: str = Field(description='状态 (unbound:未绑:gray/created:创建:blue/qr_ready:QR:blue/waiting_scan:待扫:orange/scanned:已扫:orange/confirmed:确认:blue/writing_config:写:orange/restarting_gateway:重启:orange/testing_connection:测试:blue/bound:绑定:green/expired:过期:gray/failed:失败:red/cancelled:取消:gray)')
    display_name: str | None = Field(None, description='渠道展示名')
    bound_account_display: str | None = Field(None, description='脱敏绑定账号')
    runtime_session_id: str | None = Field(None, description='Runtime 绑定 Session ID')
    expires_at: datetime | None = Field(None, description='绑定 Session 过期时间')
    metadata_json: dict | None = Field(None, description='脱敏元数据 JSON')
    last_error_code: str | None = Field(None, description='最近错误码')
    last_error_message: str | None = Field(None, description='最近错误说明')


class CreateHermesAgentChannelBindingParam(HermesAgentChannelBindingSchemaBase):
    """创建Hermes Agent 渠道绑定参数"""


class UpdateHermesAgentChannelBindingParam(HermesAgentChannelBindingSchemaBase):
    """更新Hermes Agent 渠道绑定参数"""


class DeleteHermesAgentChannelBindingParam(SchemaBase):
    """删除Hermes Agent 渠道绑定参数"""

    pks: list[int] = Field(description='Hermes Agent 渠道绑定 ID 列表')


class GetHermesAgentChannelBindingDetail(HermesAgentChannelBindingSchemaBase):
    """Hermes Agent 渠道绑定详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
