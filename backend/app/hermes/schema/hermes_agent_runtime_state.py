from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HermesAgentRuntimeStateSchemaBase(SchemaBase):
    """Hermes Agent Runtime 状态基础模型"""
    agent_id: str = Field(description='Agent 业务 ID')
    runtime_id: str | None = Field(None, description='Runtime 实例 ID')
    runtime_profile_id: str | None = Field(None, description='Runtime Profile ID')
    profile_name: str | None = Field(None, description='Hermes profile 名')
    gateway_status: str = Field(description='Gateway状态 (stopped:已停止:gray/starting:启动中:orange/running:运行中:green/restarting:重启中:orange/stopping:停止中:orange/unhealthy:不健康:red/error:异常:red)')
    gateway_restart_count: int = Field(description='Gateway 重启次数')
    gateway_started_at: datetime | None = Field(None, description='Gateway 启动时间')
    api_server_reachable: bool = Field(description='API Server 是否可达')
    terminal_backend: str = Field(description='Terminal backend (docker:Docker:blue/unknown:未知:gray)')
    container_workspace: str = Field(description='容器内工作区')
    host_workspace_display: str | None = Field(None, description='宿主机工作区脱敏展示路径')
    workspace_status: str = Field(description='Workspace状态 (unknown:未知:gray/ready:就绪:green/active:运行中:blue/error:异常:red)')
    workspace_file_count: int = Field(description='Workspace 文件数')
    workspace_bytes_used: int = Field(description='Workspace 使用字节数')
    workspace_last_write_at: datetime | None = Field(None, description='Workspace 最近写入时间')
    mount_policy: str = Field(description='挂载策略 (workspace_only:仅工作区:green/violation:存在违规:red)')
    network_policy: str = Field(description='网络策略 (unknown:未知:gray/public_outbound_internal_denied:公网可出内网阻断:green/unrestricted:不受限:orange/disabled:禁用:red)')
    network_ready: bool = Field(description='网络策略是否就绪')
    runtime_snapshot: dict | None = Field(None, description='Runtime 脱敏快照 JSON')
    last_health_at: datetime | None = Field(None, description='最近健康检查时间')
    last_error_code: str | None = Field(None, description='最近错误码')
    last_error_message: str | None = Field(None, description='最近错误说明')


class CreateHermesAgentRuntimeStateParam(HermesAgentRuntimeStateSchemaBase):
    """创建Hermes Agent Runtime 状态参数"""


class UpdateHermesAgentRuntimeStateParam(HermesAgentRuntimeStateSchemaBase):
    """更新Hermes Agent Runtime 状态参数"""


class DeleteHermesAgentRuntimeStateParam(SchemaBase):
    """删除Hermes Agent Runtime 状态参数"""

    pks: list[int] = Field(description='Hermes Agent Runtime 状态 ID 列表')


class GetHermesAgentRuntimeStateDetail(HermesAgentRuntimeStateSchemaBase):
    """Hermes Agent Runtime 状态详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
