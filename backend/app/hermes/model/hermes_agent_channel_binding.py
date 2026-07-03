from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HermesAgentChannelBinding(Base):
    """Hermes Agent 渠道绑定表"""

    __tablename__ = 'hermes_agent_channel_binding'

    id: Mapped[id_key] = mapped_column(init=False)
    binding_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='绑定业务 ID')
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 业务 ID')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    channel: Mapped[str] = mapped_column(sa.String(20), default='', comment='渠道 (feishu:飞书:blue/weixin:微信:green/qq:QQ:purple)')
    bind_mode: Mapped[str] = mapped_column(sa.String(20), default='', comment='绑定方式 (qr:扫码:green/manual:手动:blue/webhook:回调:orange)')
    status: Mapped[str] = mapped_column(sa.String(32), default='', comment='状态 (unbound:未绑:gray/created:创建:blue/qr_ready:QR:blue/waiting_scan:待扫:orange/scanned:已扫:orange/confirmed:确认:blue/writing_config:写:orange/restarting_gateway:重启:orange/testing_connection:测试:blue/bound:绑定:green/expired:过期:gray/failed:失败:red/cancelled:取消:gray)')
    display_name: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='渠道展示名')
    bound_account_display: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='脱敏绑定账号')
    runtime_session_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Runtime 绑定 Session ID')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='绑定 Session 过期时间')
    metadata_json: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='脱敏元数据 JSON')
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近错误码')
    last_error_message: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='最近错误说明')
