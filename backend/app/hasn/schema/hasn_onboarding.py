"""P0 HASN onboarding control-plane schemas.

These models mirror docs/openapi-hasn-cloud-v1.yaml for the S2 endpoints.
Do not add fields here unless the frozen OpenAPI contract changes.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.common.schema import CustomPhoneNumber, SchemaBase


class PhoneSendCodeRequest(SchemaBase):
    phone: CustomPhoneNumber = Field(description='手机号')
    purpose: Literal['login', 'bind_channel'] = Field(default='login', description='验证码用途')


class PhoneSendCodeResponse(SchemaBase):
    ok: bool = Field(description='验证码是否已发送；限流时为 false')
    retry_after_sec: int = Field(ge=0, description='再次发送前需要等待的秒数')


class PhoneVerifyRequest(SchemaBase):
    phone: CustomPhoneNumber = Field(description='手机号')
    code: str = Field(min_length=4, max_length=6, description='验证码')
    pending_intent_id: str | None = Field(default=None, description='反向 onboarding pending intent ID')


class AgentTokenInfo(SchemaBase):
    agent_hasn_id: str = Field(description='Agent HASN ID')
    agent_name: str = Field(description='Agent 显示名')
    access_token: str = Field(description='Agent JWT')
    # 已退役：JWT 不再携带 scopes claim、授权只看三态（实施102 S0）。恒空列表，
    # 仅为兼容旧 daemon 的必填反序列化字段保留（daemon Rust 侧已改 serde default）。
    scopes: list[str] = Field(default_factory=list, description='已退役占位（恒空），授权走三态 capability_modes')
    expire_time: str | None = Field(default=None, description='Token 过期时间 ISO8601 格式')
    expires_at_unix: int | None = Field(default=None, description='Token 过期 Unix 时间戳')


class PhoneVerifyResponse(SchemaBase):
    access_token: str = Field(description='HASN session access token')
    token_type: Literal['Bearer'] = Field(default='Bearer', description='Token 类型')
    expires_in_sec: int = Field(ge=1, description='访问令牌有效秒数')
    refresh_token: str | None = Field(default=None, description='刷新令牌（桌面端用于自动续期）')
    refresh_token_expire_sec: int | None = Field(default=None, description='刷新令牌有效秒数')
    # PR7: per-owner LLM credentials (newapi key + base URL). The daemon
    # writes these into each Hermes profile's `.env` file via
    # `PUT /runtime/v1/profiles/{id}/llm` so all of an owner's agents
    # share one set of LLM credentials.
    llm_token: str | None = Field(default=None, description='LLM API Token (sk-…) returned per owner')
    llm_base_url: str | None = Field(default=None, description='LLM API Base URL for the gateway')
    # 默认 LLM 模型名（如 "gpt-5.5", "qwen-max"）。daemon 把它写入每个 hermes
    # profile 的 config.yaml `model.default`。后续支持按用户级别区分模型时
    # 直接改 service 注入逻辑，daemon 端无需变更。
    llm_model: str | None = Field(default=None, description='默认 LLM 模型名，写入 hermes profile config')
    agent_tokens: list[AgentTokenInfo] = Field(default_factory=list, description='Agent JWT 列表')


class HasnTokenRefreshRequest(SchemaBase):
    refresh_token: str = Field(description='刷新令牌')


class HasnTokenRefreshResponse(SchemaBase):
    access_token: str = Field(description='新的访问令牌')
    expires_in_sec: int = Field(ge=1, description='访问令牌有效秒数')
    refresh_token: str = Field(description='新的刷新令牌（轮换）')
    refresh_token_expire_sec: int = Field(ge=1, description='刷新令牌有效秒数')


class NodeClaim(SchemaBase):
    node_id: str = Field(description='HASN Node ID')
    device_name: str = Field(description='设备名称')
    platform: str = Field(description='设备平台')
    client_version: str | None = Field(default=None, description='客户端版本')


class ClientInfo(SchemaBase):
    protocol: str = Field(default='hasn/0.2', description='客户端协议版本')
    supported_extensions: list[str] | None = Field(default=None, description='支持的扩展列表')


class OnboardingEnsureRequest(SchemaBase):
    node: NodeClaim = Field(description='当前 hasn-node 声明')
    client: ClientInfo = Field(description='客户端能力摘要')
    pending_intent_id: str | None = Field(default=None, description='反向 onboarding pending intent ID')


class HumanSummary(SchemaBase):
    human_id: str = Field(description='Human HASN ID')
    owner_id: str = Field(description='Owner HASN ID')
    display_name: str | None = Field(default=None, description='显示名称')


class OwnerBindingSummary(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    node_id: str = Field(description='Node ID')
    status: Literal['active', 'expiring', 'revoked'] = Field(description='绑定状态')
    revision: int = Field(description='绑定修订号')


class AgentSummary(SchemaBase):
    agent_id: str = Field(description='默认 Agent ID')
    owner_id: str = Field(description='Owner HASN ID')
    hasn_id: str = Field(description='默认 Agent HASN ID')
    # PR1.5: daemon 端把 backend 返回的 hasn_id 错误塞进本地 star_id 字段，
    # 导致绑定按钮报 "agent star_id is empty"。修复：onboarding/ensure 把
    # hasn_agents 表中的 star_id 字段透传，daemon 用真实 star_id 写本地。
    star_id: str = Field(description='默认 Agent 唤星号 (e.g. 100001#star)')
    display_name: str | None = Field(default=None, description='默认 Agent 显示名称')
    # Agent JWT 认证信息
    access_token: str = Field(description='Agent JWT access token')
    # 已退役：JWT 不再携带 scopes claim、授权只看三态（实施102 S0）。恒空列表，
    # 仅为兼容旧 daemon 的必填反序列化字段保留（daemon Rust 侧已改 serde default）。
    scopes: list[str] = Field(default_factory=list, description='已退役占位（恒空），授权走三态 capability_modes')
    expire_time: str = Field(description='Token 过期时间 ISO8601 格式')


class SandboxSummary(SchemaBase):
    sandbox_id: str = Field(description='Sandbox ID')
    status: Literal['creating', 'active', 'sleeping', 'deleted', 'failed'] = Field(description='Sandbox 状态')
    base_url: str | None = Field(default=None, description='Tenant router base URL')


class OnboardingEnsureResponse(SchemaBase):
    human: HumanSummary = Field(description='Human / Owner 摘要')
    owner_binding: OwnerBindingSummary = Field(description='Node ↔ Owner 绑定摘要')
    default_agent: AgentSummary = Field(description='默认 Agent 摘要')
    sandbox: SandboxSummary | None = Field(default=None, description='Sandbox 路由摘要；S3 未创建时为空')
    sync_cursor: str = Field(description='bootstrap sync cursor')
