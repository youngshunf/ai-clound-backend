from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HermesAgentSchemaBase(SchemaBase):
    """Hermes Agent 基础模型"""
    agent_id: str = Field(description='Agent 业务 ID')
    user_id: int = Field(description='用户 ID')
    agent_name: str = Field(description='Agent 名称')
    template: str = Field(description='模板 (assistant:通用助理:blue/office:办公助理:cyan/creator:内容创作:purple/custom:自定义:gray)')
    timezone: str = Field(description='时区')
    status: str = Field(description='Agent状态 (creating:创建中:orange/created:已创建:blue/ready:就绪:cyan/running:运行中:green/stopped:已停止:gray/error:异常:red/deleting:删除中:orange/deleted:已删除:gray)')
    runtime_id: str | None = Field(None, description='Runtime 实例 ID')
    runtime_profile_id: str | None = Field(None, description='Runtime Profile ID')
    profile_name: str | None = Field(None, description='Hermes profile 名')
    llm_mode: str = Field(description='LLM模式 (platform:平台托管:green/byok:用户自带:blue)')
    llm_provider: str = Field(description='LLM Provider')
    llm_model: str | None = Field(None, description='LLM 模型')
    gateway_status: str = Field(description='Gateway状态 (stopped:已停止:gray/starting:启动中:orange/running:运行中:green/restarting:重启中:orange/stopping:停止中:orange/unhealthy:不健康:red/error:异常:red)')
    workspace_status: str = Field(description='Workspace状态 (unknown:未知:gray/ready:就绪:green/active:运行中:blue/error:异常:red)')
    sandbox_status: str = Field(description='Sandbox状态 (unknown:未知:gray/ready:就绪:green/unprotected:未保护:orange/error:异常:red)')
    channel_count: int = Field(description='已绑定渠道数')
    last_active_at: datetime | None = Field(None, description='最近活跃时间')
    last_runtime_sync_at: datetime | None = Field(None, description='最近 Runtime 同步时间')
    last_error_code: str | None = Field(None, description='最近错误码')
    last_error_message: str | None = Field(None, description='最近错误说明')
    remark: str | None = Field(None, description='备注')
    deleted_time: datetime | None = Field(None, description='软删除时间')


class CreateHermesAgentParam(HermesAgentSchemaBase):
    """创建Hermes Agent 参数"""


class UpdateHermesAgentParam(HermesAgentSchemaBase):
    """更新Hermes Agent 参数"""


class DeleteHermesAgentParam(SchemaBase):
    """删除Hermes Agent 参数"""

    pks: list[int] = Field(description='Hermes Agent  ID 列表')


class GetHermesAgentDetail(HermesAgentSchemaBase):
    """Hermes Agent 详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
