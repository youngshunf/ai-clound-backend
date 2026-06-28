"""第三方 MCP 网关管理面请求体（P7-D，事实源 10 §7.1 / 实施 99 P7-D）。

owner 面（Owner JWT）：注册 remote_service server、写/轮换/撤销凭据、绑定 Agent、启停。
admin 面（RBAC）：system-origin 平台 server + 平台 key + per-owner 配额/限流。

明文凭据仅前端→后端单向提交，后端加密落库；**永不回显**（请求体有 credential 字段，
出参绝无明文）。身份取自 JWT（owner_hasn_id / admin），故请求体不含归属字段。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterOwnerServerParam(BaseModel):
    """[Owner] 注册一个第三方 MCP（origin 恒 owner）。

    两种承载（doc101 §0.1，origin 恒 owner——平台 key 走 admin/remote_service，绝不经此）：
    - `remote_service`（默认）：云端直连 SaaS MCP，必填 `endpoint`（http/https/ws/wss）。
    - `local_process`：设备本机 spawn 的 stdio / 回环 http MCP，stdio 必填 `command`，可带 `args`/`env`。
      `env` 值含凭据必须是 `secret://` 引用（明文不入配置/不入同步，02 §6）；建连时 daemon 经
      resolve-env 端点实时解析注入子进程。
    """

    name: str = Field(min_length=1, max_length=64, description='server_namespace（全局唯一，映射 hasn.ext.{name}.*）')
    display_name: str | None = Field(default=None, max_length=120, description='中文展示名')
    hosting: str = Field(default='remote_service', description='承载 remote_service（云端直连）/ local_process（本机 spawn）')
    transport: str = Field(default='http', description='传输 http/websocket/sse（remote）或 stdio（local_process）')
    endpoint: str | None = Field(default=None, max_length=1024, description='远程/回环端点 URL（http/https/ws/wss；stdio 不填）')
    # local_process 专属：本机 spawn 的可执行 + 参数 + 环境变量（env 值含凭据须 secret:// 引用）。
    command: str | None = Field(default=None, max_length=512, description='[local_process stdio] 直 exec 的可执行（不经 shell）')
    args: list[str] = Field(default_factory=list, description='[local_process] spawn 参数数组')
    env: dict[str, str] = Field(default_factory=dict, description='[local_process] 子进程 env（凭据值须 secret:// 引用）')
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
