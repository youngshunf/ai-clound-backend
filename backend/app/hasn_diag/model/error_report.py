from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_diag.model._base import HasnDiagAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class ErrorReport(HasnDiagAppBase):
    """错误事件 occurrence（原始证据流·(node_id,dedup_key) 幂等·TTL 90 天）"""

    __tablename__ = 'error_report'

    id: Mapped[id_key] = mapped_column(init=False)
    node_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='上报设备 node_id（客户端自报）')
    owner_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='归属主人 hasn_id（可空）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='归属分身 hasn_id（可空）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (daemon/hermes/runtime)')
    severity: Mapped[str] = mapped_column(sa.String(16), default='', comment='严重度 (critical/error/warn)')
    fingerprint: Mapped[str] = mapped_column(sa.String(64), default='', comment='归类键（模块级位置·无行号）')
    dedup_key: Mapped[str] = mapped_column(sa.String(96), default='', comment='单次物理发生幂等键（§3）')
    error_class: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='异常类/错误码')
    message: Mapped[str] = mapped_column(UniversalText, default='', comment='脱敏后错误消息')
    location: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='file:line / logger 名')
    context_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='结构化上下文 jsonb')
    suppressed_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='采样被抑制的同 fingerprint 次数')
    app_version: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='客户端版本')
    platform: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='平台 (macos/windows/linux/...)')
    occurred_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='客户端真实发生时刻')
