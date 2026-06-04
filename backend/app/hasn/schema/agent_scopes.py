"""Agent 权限管理 Schema"""

from pydantic import Field

from backend.common.schema import SchemaBase


class AgentScopesConfig(SchemaBase):
    """Agent 权限配置（含三态 default_mode/capability_modes）"""

    scopes: list[str] = Field(default_factory=list, description='Agent 权限列表（审计快照）')
    post_needs_review: bool = Field(default=False, description='发帖是否需要审核')
    default_mode: str = Field(default='allow', description='默认模式 allow|ask|deny')
    capability_modes: dict[str, str] = Field(default_factory=dict, description='每能力 override {key: allow|ask|deny}')


class UpdateAgentScopesRequest(SchemaBase):
    """更新 Agent 权限请求（三态；scopes 为兼容字段，可选）"""

    default_mode: str = Field(default='allow', description='默认模式 allow|ask|deny')
    capability_modes: dict[str, str] = Field(default_factory=dict, description='每能力 override {key: allow|ask|deny}')
    post_needs_review: bool | None = Field(default=None, description='发帖是否需要审核（None 表示不改）')
    scopes: list[str] | None = Field(default=None, description='兼容字段，可选（审计快照）')


class UpdateAgentScopesResponse(SchemaBase):
    """更新 Agent 权限响应（D3 不重签 JWT，返回最新配置）"""

    config: AgentScopesConfig = Field(description='更新后的权限配置')


class ScopeCapability(SchemaBase):
    """catalog 单条能力（= 一个 scope）"""

    key: str = Field(description='能力 key（scope）')
    label: str = Field(description='中文显示名')
    domain: str = Field(default='', description='所属域')
    risk: str = Field(default='low', description='风险等级 low|medium|high（仅 UI 提示）')
    description: str = Field(default='', description='用途描述')
    mode: str = Field(description='当前三态 allow|ask|deny')
    tools: list[str] = Field(default_factory=list, description='覆盖的工具 canonical 名')


class ScopeSource(SchemaBase):
    """catalog 一个来源分组"""

    source: str = Field(description='来源 platform|app|external')
    label: str = Field(description='来源中文名')
    capabilities: list[ScopeCapability] = Field(default_factory=list)


class ScopeCatalogResponse(SchemaBase):
    """工具/scope 目录（按来源分组，每条带三态）"""

    default_mode: str = Field(default='allow')
    sources: list[ScopeSource] = Field(default_factory=list)


class AskRequestItem(SchemaBase):
    """一条挂起的 ask 批准请求（主人 UI 列出）"""

    request_id: str = Field(description='挂起请求 ID')
    agent_hasn_id: str = Field(default='', description='发起调用的 Agent')
    owner_hasn_id: str | None = Field(default=None, description='所属主人')
    tool_name: str = Field(description='待批准的工具')
    description: str = Field(default='', description='人类可读审批描述（NLG）')
    args_digest: dict = Field(default_factory=dict, description='入参脱敏摘要（供主人判断，不含敏感原文）')
    expires_time: str | None = Field(default=None, description='审批超时时间 ISO')


class AskRequestsResponse(SchemaBase):
    """某 Agent 当前挂起的 ask 请求列表"""

    requests: list[AskRequestItem] = Field(default_factory=list)


class AskDecisionRequest(SchemaBase):
    """主人对挂起请求的决定"""

    decision: str = Field(description='approve|reject（approved/rejected 亦可）')


class GrantApprovalRequest(SchemaBase):
    """主人批准一条 ask 审批请求（签一次性票据，令牌重试 doc15 §3.3）"""

    scope: str = Field(default='once', description='授权粒度 once（本次）|always（总是，写回 capability_modes=allow）')
