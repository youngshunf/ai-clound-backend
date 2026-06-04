from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAgentApprovalRequestsSchemaBase(SchemaBase):
    """HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）基础模型"""
    request_id: str = Field(description='审批请求业务 ID（areq_{ulid}）')
    agent_hasn_id: str = Field(description='发起调用的 Agent hasn_id')
    owner_hasn_id: str = Field(description='审批人（主人）hasn_id')
    tool_name: str = Field(description='被调用的工具 canonical name')
    args_hash: str = Field(description='入参 canonical JSON 的 sha256（票据绑定，防换参重放）')
    args_digest: dict = Field(description='入参脱敏摘要 JSON（卡片展示用，不存敏感原文）')
    capability_keys: list = Field(default_factory=list, description='触发 ask 的能力 key 列表（总是允许时据此写回 capability_modes=allow）')
    description: str = Field(description='人类可读的审批描述（NLG，卡片标题/正文）')
    status: str = Field(description='审批状态 (pending:待审:orange/approved:已批:green/denied:已拒:red/timeout:超时:gray/consumed:已用:blue)')
    grant_scope: str | None = Field(None, description='授权粒度 (once:本次:blue/always:总是:green)')
    ticket_jti: str | None = Field(None, description='签发的一次性票据 jti（防重放追踪）')
    decided_time: datetime | None = Field(None, description='主人决定时间')
    expires_time: datetime = Field(description='审批超时时间（默认 now+600s）')


class CreateHasnAgentApprovalRequestsParam(HasnAgentApprovalRequestsSchemaBase):
    """创建HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数"""


class UpdateHasnAgentApprovalRequestsParam(HasnAgentApprovalRequestsSchemaBase):
    """更新HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数"""


class DeleteHasnAgentApprovalRequestsParam(SchemaBase):
    """删除HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数"""

    pks: list[int] = Field(description='HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID 列表')


class GetHasnAgentApprovalRequestsDetail(HasnAgentApprovalRequestsSchemaBase):
    """HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
