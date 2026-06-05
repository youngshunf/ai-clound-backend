from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class AiNativeAppAuditSchemaBase(SchemaBase):
    trace_id: str = Field(description='trace id')
    step: str = Field(description='审计步骤')
    workspace_kind: str = Field(description='workspace 类型')
    user_id: int | None = Field(None, description='个人空间用户 ID')
    enterprise_id: int | None = Field(None, description='企业 ID')
    app_id: str = Field(description='应用 ID')
    app_version: str | None = Field(None, description='版本号')
    actor_type: str = Field(description='actor 类型')
    agent_hasn_id: str | None = Field(None, description='Agent HASN ID')
    owner_hasn_id: str | None = Field(None, description='Owner HASN ID')
    session_uuid: str | None = Field(None, description='会话 UUID')
    method: str = Field(description='调用方法')
    capability_id: str | None = Field(None, description='能力 ID')
    tool_id: str | None = Field(None, description='Tool ID')
    event_type: str | None = Field(None, description='事件类型')
    required_scopes: list[str] = Field(default_factory=list, description='必需 scopes')
    agent_scopes_snapshot: list[str] = Field(default_factory=list, description='Agent scopes 快照')
    workspace_role: str | None = Field(None, description='workspace 角色')
    risk_level: str | None = Field(None, description='风险等级')
    decision: str = Field(description='allow/deny')
    confirmation_id: str | None = Field(None, description='确认单 ID')
    result_ref: str | None = Field(None, description='结果引用')
    error_code: str | None = Field(None, description='错误码')
    context: dict = Field(default_factory=dict, description='额外上下文')


class CreateAiNativeAppAuditParam(AiNativeAppAuditSchemaBase):
    pass


class ReportAiNativeAppAuditParam(SchemaBase):
    """Agent 自报「本地执行工具」调用审计的入参（D5，PRES-P4-d）。

    身份（`agent_hasn_id`/`owner_hasn_id`）与 `actor_type` **由 Agent JWT 注入、服务端权威**，
    不取 body（identity by auth，防冒名）；其余字段由分身侧本地 `AuthorizationAudit` 提供。
    """

    trace_id: str = Field(description='trace id（分身侧生成，幂等/排错用）')
    app_id: str = Field(description='应用 ID（如 presentation）')
    method: str = Field(description='调用方法（工具名，如 hasn.presentation.generate）')
    decision: str = Field(description='allow/deny/ask')
    step: str = Field('local_tool', description='审计步骤（本地工具固定 local_tool）')
    workspace_kind: str = Field('personal', description='workspace 类型（embedded_desktop 本地默认 personal）')
    app_version: str | None = Field(None, description='版本号')
    session_uuid: str | None = Field(None, description='会话 UUID')
    capability_id: str | None = Field(None, description='能力 ID')
    tool_id: str | None = Field(None, description='Tool ID')
    event_type: str | None = Field(None, description='事件类型')
    required_scopes: list[str] = Field(default_factory=list, description='必需 scopes')
    risk_level: str | None = Field(None, description='风险等级')
    result_ref: str | None = Field(None, description='结果引用')
    error_code: str | None = Field(None, description='错误码')
    context: dict = Field(default_factory=dict, description='额外上下文（脱敏摘要等）')


class UpdateAiNativeAppAuditParam(AiNativeAppAuditSchemaBase):
    pass


class DeleteAiNativeAppAuditParam(SchemaBase):
    pks: list[int] = Field(description='审计 ID 列表')


class GetAiNativeAppAuditDetail(AiNativeAppAuditSchemaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
