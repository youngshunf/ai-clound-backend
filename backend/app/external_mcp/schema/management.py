"""第三方 MCP 网关管理面请求体（P7-D，事实源 10 §7.1 / 实施 99 P7-D）。

owner 面（Owner JWT）：注册 remote_service server、写/轮换/撤销凭据、绑定 Agent、启停。
admin 面（RBAC）：system-origin 平台 server + 平台 key + per-owner 配额/限流。

明文凭据仅前端→后端单向提交，后端加密落库；**永不回显**（请求体有 credential 字段，
出参绝无明文）。身份取自 JWT（owner_hasn_id / admin），故请求体不含归属字段。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterOwnerServerParam(BaseModel):
    """[Owner] 注册一个 remote_service 第三方 MCP（hosting 恒 remote_service，origin 恒 owner）。"""

    name: str = Field(min_length=1, max_length=64, description='server_namespace（全局唯一，映射 hasn.ext.{name}.*）')
    display_name: str | None = Field(default=None, max_length=120, description='中文展示名')
    transport: str = Field(default='http', description='传输 http/websocket/sse（remote_service）')
    endpoint: str = Field(min_length=1, max_length=1024, description='远程端点 URL（http/https/ws/wss）')
    risk_level: str = Field(default='medium', description='风险等级 low/medium/high')
    # 凭据（可选，提供则注册后立即写入并接进 header 模板）。
    credential: str | None = Field(default=None, description='明文凭据（如 bearer token），写后永不回显')
    auth_header: str = Field(default='Authorization', max_length=64, description='凭据注入的请求头名')
    auth_scheme: str = Field(default='Bearer', max_length=32, description='凭据前缀（如 Bearer；留空=裸值）')


class RegisterSystemServerParam(RegisterOwnerServerParam):
    """[Admin] 注册 system-origin 平台 server（含 per-owner 配额/限流）。"""

    per_owner_daily_quota: int = Field(default=0, ge=0, description='per-owner 每日调用配额（0=不限）')
    rate_limit_per_min: int = Field(default=0, ge=0, description='per-owner 每分钟限流（0=不限）')


class SetCredentialParam(BaseModel):
    """[Owner/Admin] 写入或轮换 server 凭据（明文→加密；同 URI 覆盖=轮换）。"""

    credential: str = Field(min_length=1, description='明文凭据，写后永不回显')
    auth_header: str = Field(default='Authorization', max_length=64, description='凭据注入的请求头名')
    auth_scheme: str = Field(default='Bearer', max_length=32, description='凭据前缀（留空=裸值）')


class SetServerStatusParam(BaseModel):
    """[Owner/Admin] 启用/停用 server。"""

    status: str = Field(description='active 启用 / disabled 停用')


class SetServerQuotaParam(BaseModel):
    """[Admin] 配 system-origin 平台 key 的 per-owner 配额/限流（10 §7.2）。"""

    per_owner_daily_quota: int = Field(default=0, ge=0, description='per-owner 每日调用配额（0=不限）')
    rate_limit_per_min: int = Field(default=0, ge=0, description='per-owner 每分钟限流（0=不限）')


class CreateBindingParam(BaseModel):
    """[Owner] 授权某 Agent 可用某 server 的若干工具（不传 allowed_raw_names=全量授权）。"""

    agent_hasn_id: str = Field(min_length=1, max_length=64, description='被授权 Agent hasn_id（必须本人名下）')
    mcp_id: str = Field(min_length=1, max_length=40, description='目标 server mcp_id（本人自配或 system）')
    allowed_raw_names: list[str] | None = Field(default=None, description='授权的第三方原始工具名（None=全量）')


class SetBindingEnabledParam(BaseModel):
    """[Owner] 临时启用/停用某 Agent↔server 绑定。"""

    agent_hasn_id: str = Field(min_length=1, max_length=64, description='Agent hasn_id')
    mcp_id: str = Field(min_length=1, max_length=40, description='server mcp_id')
    enabled: bool = Field(description='true 启用 / false 停用')
