from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HermesAgentLlmTokenSchemaBase(SchemaBase):
    """Hermes Agent 级 LLM token 隔离记录基础模型"""
    agent_id: str = Field(description='Agent 业务 ID')
    user_id: int = Field(description='唤星用户 ID')
    newapi_user_id: int = Field(description='new-api users.id')
    newapi_token_id: int = Field(description='new-api tokens.id')
    token_key_prefix: str = Field(description='token 明文前 8 字符（脱敏展示与审计）')
    token_key_sha256: str = Field(description='token 明文 SHA256（反查匹配，不可逆）')
    model_allowlist: list[str] | None = Field(None, description='平台模型白名单 JSON 数组，留空 = 跟随 user 默认')
    rate_limit_rps: int | None = Field(None, description='单 Agent QPS 限速，留空 = 跟随 user 默认')
    per_token_quota_remaining: int | None = Field(None, description='可选：单 token 独立配额；留空 = 与 user.quota 共享')
    issued_at: datetime = Field(description='签发时间')
    revoked_at: datetime | None = Field(None, description='撤销时间，NULL 表示有效')
    runtime_node_id: str | None = Field(None, description='Runtime 节点 ID（预留 §08）')


class CreateHermesAgentLlmTokenParam(HermesAgentLlmTokenSchemaBase):
    """创建Hermes Agent 级 LLM token 隔离记录参数"""


class UpdateHermesAgentLlmTokenParam(HermesAgentLlmTokenSchemaBase):
    """更新Hermes Agent 级 LLM token 隔离记录参数"""


class DeleteHermesAgentLlmTokenParam(SchemaBase):
    """删除Hermes Agent 级 LLM token 隔离记录参数"""

    pks: list[int] = Field(description='Hermes Agent 级 LLM token 隔离记录 ID 列表')


class GetHermesAgentLlmTokenDetail(HermesAgentLlmTokenSchemaBase):
    """Hermes Agent 级 LLM token 隔离记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
