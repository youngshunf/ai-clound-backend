from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnGroupImCommandOutbox(Base):
    """群邀请等群业务状态的事务 IM 命令队列"""

    __tablename__ = 'hasn_group_im_command_outbox'

    id: Mapped[id_key] = mapped_column(init=False)
    command_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='命令公开标识')
    producer: Mapped[str] = mapped_column(sa.String(40), default='', comment='生产方固定标识 group')
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='ensure 后取得的权威直聊会话 ID')
    command_type: Mapped[str] = mapped_column(sa.String(64), default='', comment='命令类型，当前仅 send_message')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='版本化的认证主体与发送命令 JSON')
    payload_hash: Mapped[str] = mapped_column(sa.CHAR(), default='', comment='规范化命令载荷 SHA-256')
    idempotency_key: Mapped[str] = mapped_column(sa.String(160), default='', comment='群业务对象派生的稳定 IM 幂等键')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='投递状态：pending/processing/completed/dead_letter')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已失败次数')
    next_attempt_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下次允许领取时间')
    lease_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='处理中租约截止时间')
    locked_by: Mapped[str | None] = mapped_column(sa.String(160), default=None, comment='当前 relay 实例标识')
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最近一次失败诊断')
    message_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='成功投递后的权威消息 ID')
    trace_id: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='跨服务追踪标识')
    causation_id: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='触发本命令的群邀请标识')
    completed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='投递完成时间')
