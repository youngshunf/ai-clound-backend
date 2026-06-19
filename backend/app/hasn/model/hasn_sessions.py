from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnSessions(Base):
    """HASN 会话分层 - 逻辑会话表"""

    __tablename__ = 'hasn_sessions'

    session_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, comment='Session ID (ULID)')
    conversation_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联的 conversation ID')
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Owner ID')
    hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent ID')
    session_kind: Mapped[str] = mapped_column(sa.String(20), default='', comment='会话类型 (interactive/task/temporary/external/system)')
    session_scope: Mapped[str] = mapped_column(sa.String(20), default='', comment='同步范围 (conversation_visible/summary_only/local_only)')
    session_status: Mapped[str] = mapped_column(sa.String(20), default='active', comment='会话状态 (active/completed/error/cancelled)')
    parent_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='父会话 ID (用于分叉)')
    continuation_from_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='续接自哪个 Session')
    origin_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='来源类型 (ui/scheduler/external_app/api/system/task_run/workflow_run/app)；app=AI-Native 应用工作会话(AppCollab doc21 §D3)')
    origin_ref: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='来源引用 (task_id/app_id/trace_id)；origin_type=app 时形如 resource:<app>:<id>(AppCollab doc21 §D3)')
    title: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='Session 标题')
    summary_checkpoint_json: Mapped[dict | None] = mapped_column(sa.JSON(), default=None, comment='会话摘要检查点 (JSON)')
    active_binding_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='当前活跃的绑定 ID')
    last_message_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最后一条消息 ID')
    last_message_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后消息时间')
    closed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='关闭时间')
