from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAgentRuntimeReportsSchemaBase(SchemaBase):
    """HASN Agent Runtime 脱敏摘要上报基础模型"""
    report_id: str = Field(description='Runtime report 唯一 ID (rr_{uuid})')
    owner_id: str = Field(description='Agent Owner hasn_id')
    agent_hasn_id: str = Field(description='Agent hasn_id')
    node_id: str = Field(description='上报 Node ID')
    runtime_type: str = Field(description='Runtime 类型 (claude_code:Claude Code:purple/codex:Codex:blue/hermes:Hermes:green/webhook:Webhook:orange/cloud_sdk:Cloud SDK:cyan/none:无:gray)')
    runtime_status: str = Field(description='Runtime 状态 (online:在线:green/offline:离线:gray/unavailable:不可用:orange/error:错误:red/unknown:未知:gray)')
    adapter_registered: bool = Field(description='RuntimeAdapter 是否已注册')
    handle_available: bool = Field(description='RuntimeHandle 是否可调度')
    binding_id: str | None = Field(None, description='公共 Binding ID 摘要（可空）')
    runtime_revision: int = Field(description='Runtime 摘要修订号')
    summary_json: dict = Field(description='脱敏 Runtime Summary；禁止 workspace/endpoint/PID/CLI args/OAuth path')
    last_seen_at: datetime | None = Field(None, description='Runtime 最后可见时间')
    reported_at: datetime = Field(description='上报时间')


class CreateHasnAgentRuntimeReportsParam(HasnAgentRuntimeReportsSchemaBase):
    """创建HASN Agent Runtime 脱敏摘要上报参数"""


class UpdateHasnAgentRuntimeReportsParam(HasnAgentRuntimeReportsSchemaBase):
    """更新HASN Agent Runtime 脱敏摘要上报参数"""


class DeleteHasnAgentRuntimeReportsParam(SchemaBase):
    """删除HASN Agent Runtime 脱敏摘要上报参数"""

    pks: list[int] = Field(description='HASN Agent Runtime 脱敏摘要上报 ID 列表')


class GetHasnAgentRuntimeReportsDetail(HasnAgentRuntimeReportsSchemaBase):
    """HASN Agent Runtime 脱敏摘要上报详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
