from datetime import datetime

from pydantic import Field

from backend.app.admin.schema.user import GetUserInfoDetail
from backend.common.enums import StatusType
from backend.common.schema import SchemaBase


class GetSwaggerToken(SchemaBase):
    """Swagger 认证令牌"""

    access_token: str = Field(description='访问令牌')
    token_type: str = Field('Bearer', description='令牌类型')
    user: GetUserInfoDetail = Field(description='用户信息')


class AccessTokenBase(SchemaBase):
    """访问令牌基础模型"""

    access_token: str = Field(description='访问令牌')
    access_token_expire_time: datetime = Field(description='令牌过期时间')
    session_uuid: str = Field(description='会话 UUID')


class RefreshTokenParam(SchemaBase):
    """刷新令牌请求参数（body 方式传入 refresh_token，兼容不使用 Cookie 的客户端）"""

    refresh_token: str | None = Field(None, description='刷新令牌（不传则从 Cookie 读取）')


class GetNewToken(AccessTokenBase):
    """获取新令牌"""

    new_refresh_token: str | None = Field(None, description='新的刷新令牌（轮换后）')
    new_refresh_token_expire_time: datetime | None = Field(None, description='新刷新令牌过期时间')


class AgentTokenInfo(SchemaBase):
    """Agent JWT 信息"""

    agent_hasn_id: str = Field(description='Agent HASN ID')
    agent_name: str = Field(description='Agent 显示名')
    access_token: str = Field(description='Agent JWT')
    # 已退役：JWT 不再携带 scopes claim、授权只看三态（实施102 S0）。恒空列表，
    # 仅为兼容旧 daemon 的必填反序列化字段保留（daemon Rust 侧已改 serde default）。
    scopes: list[str] = Field(default_factory=list, description='已退役占位（恒空），授权走三态 capability_modes')


class GetLoginToken(AccessTokenBase):
    """获取登录令牌"""

    refresh_token: str = Field(description='刷新令牌')
    refresh_token_expire_time: datetime = Field(description='刷新令牌过期时间')
    password_expire_days_remaining: int | None = Field(None, description='密码过期剩余天数')
    llm_token: str = Field(description='LLM API Key')
    llm_base_url: str | None = Field(default=None, description='LLM API Base URL')
    hasn_node_key: str | None = Field(default=None, description='HASN Node Key（hasn_nk_ 前缀）')
    agent_tokens: list[AgentTokenInfo] = Field(default_factory=list, description='Agent JWT 列表')
    user: GetUserInfoDetail = Field(description='用户信息')


class GetTokenDetail(SchemaBase):
    """令牌详情"""

    id: int = Field(description='用户 ID')
    session_uuid: str = Field(description='会话 UUID')
    username: str = Field(description='用户名')
    nickname: str = Field(description='昵称')
    ip: str = Field(description='IP 地址')
    os: str = Field(description='操作系统')
    browser: str = Field(description='浏览器')
    device: str = Field(description='设备')
    status: StatusType = Field(description='状态')
    last_login_time: str = Field(description='最后登录时间')
    expire_time: datetime = Field(description='过期时间')
