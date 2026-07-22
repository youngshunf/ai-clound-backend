from datetime import datetime
from typing import Any

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.external_mcp.model._base import ExternalMcpAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class ExternalMcpServer(ExternalMcpAppBase):
    """第三方 MCP server 配置（网关自省与代理的承载单元；owner 自配或平台 system 预置）"""

    __tablename__ = 'external_mcp_server'

    id: Mapped[id_key] = mapped_column(init=False)
    mcp_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='server 业务主键 mcp_{ulid}（注册时生成，全局唯一）')
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='server_namespace（如 qcc/gmail，全局唯一，映射 hasn.ext.{name}.*）')
    display_name: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='展示名（中文友好名，UI 用）')
    hosting: Mapped[str] = mapped_column(sa.String(20), default='', comment='承载形态 (remote_service:远程服务型:blue/local_process:本地进程型:green)')
    transport: Mapped[str] = mapped_column(sa.String(20), default='', comment='传输 (http:HTTP/websocket:WebSocket/sse:SSE/stdio:本机stdio)')
    endpoint: Mapped[str | None] = mapped_column(sa.String(1024), default=None, comment='远程端点 URL（remote_service 必填；http/ws/sse）')
    command: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='本机启动命令（local_process stdio，如 npx）')
    args: Mapped[list[str]] = mapped_column(postgresql.JSONB(), default_factory=list, comment='本机启动参数 jsonb（local_process）')
    env: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='环境变量 jsonb（值可为 secret:// 引用；明文拒绝）')
    headers: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='请求头 jsonb（remote_service；值可为 secret:// 引用，如 Authorization）')
    origin: Mapped[str] = mapped_column(sa.String(20), default='', comment='配置归属 (system:平台预置:gold/owner:用户自配:blue/marketplace:市场安装:purple)')
    owner_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='归属主人 hasn_id（owner/marketplace-origin 必填；system-origin 为空=全平台共享）')
    scope: Mapped[str] = mapped_column(sa.String(20), default='', comment='可见范围（owner=该主人及其 Agent）')
    risk_level: Mapped[str] = mapped_column(sa.String(16), default='', comment='风险等级 (low:低:green/medium:中:orange/high:高:red)')
    advertised_tools_cache: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='自省到的第三方工具缓存 jsonb（tools/list 归一 ToolMeta[]，目录层缓存非调用缓存）'
    )
    tools_hash: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='工具集 hash（tools/list 内容指纹，变更触发 re-probe / SCHEMA_HASH_MISMATCH）')
    advertised_tools_cached_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='工具缓存刷新时间')
    health_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='健康 (unknown:未知/unstarted:未拉起/healthy:健康:green/unhealthy:不健康:orange/circuit_broken:熔断:red/not_installed:未安装)')
    health_checked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近 probe 时间')
    health_detail: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='健康详情（失败真实原因，零 fake）')
    per_owner_daily_quota: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='system-origin 平台 key 的 per-owner 每日调用配额（0=不限；10 §7.2）')
    rate_limit_per_min: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='system-origin 平台 key 的 per-owner 每分钟限流（0=不限；10 §7.2）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:启用:green/disabled:停用:red)')
