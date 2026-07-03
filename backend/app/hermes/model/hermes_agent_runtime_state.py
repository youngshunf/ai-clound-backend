from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HermesAgentRuntimeState(Base):
    """Hermes Agent Runtime 状态表"""

    __tablename__ = 'hermes_agent_runtime_state'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 业务 ID')
    runtime_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='Runtime 实例 ID')
    runtime_profile_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Runtime Profile ID')
    profile_name: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Hermes profile 名')
    gateway_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Gateway状态 (stopped:已停止:gray/starting:启动中:orange/running:运行中:green/restarting:重启中:orange/stopping:停止中:orange/unhealthy:不健康:red/error:异常:red)')
    gateway_restart_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='Gateway 重启次数')
    gateway_started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='Gateway 启动时间')
    api_server_reachable: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='API Server 是否可达')
    terminal_backend: Mapped[str] = mapped_column(sa.String(16), default='', comment='Terminal backend (docker:Docker:blue/unknown:未知:gray)')
    container_workspace: Mapped[str] = mapped_column(sa.String(64), default='', comment='容器内工作区')
    host_workspace_display: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='宿主机工作区脱敏展示路径')
    workspace_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Workspace状态 (unknown:未知:gray/ready:就绪:green/active:运行中:blue/error:异常:red)')
    workspace_file_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='Workspace 文件数')
    workspace_bytes_used: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='Workspace 使用字节数')
    workspace_last_write_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='Workspace 最近写入时间')
    mount_policy: Mapped[str] = mapped_column(sa.String(32), default='', comment='挂载策略 (workspace_only:仅工作区:green/violation:存在违规:red)')
    network_policy: Mapped[str] = mapped_column(sa.String(64), default='', comment='网络策略 (unknown:未知:gray/public_outbound_internal_denied:公网可出内网阻断:green/unrestricted:不受限:orange/disabled:禁用:red)')
    network_ready: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='网络策略是否就绪')
    runtime_snapshot: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='Runtime 脱敏快照 JSON')
    last_health_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近健康检查时间')
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近错误码')
    last_error_message: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='最近错误说明')
