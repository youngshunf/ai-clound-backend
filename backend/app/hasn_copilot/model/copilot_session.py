from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase
from backend.common.model import TimeZone, id_key


class CopilotSession(CopilotBase):
    """会议副驾会话元数据（云端权威）"""

    __tablename__ = 'copilot_session'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归属 owner HASN ID（owner 隔离键，所有查询强制带；引用 public.hasn_humans）'
    )
    session_id: Mapped[str] = mapped_column(
        sa.String(64),
        default='',
        comment='工作会话 id（任务系统 session_kind=task/summary_only，直连 hermes；转写/建议都在此会话内，不在 conversation）',
    )
    bound_agent_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='协作分身 HASN ID（owner 名下 a_* 分身，会话级快照；null=未绑定）'
    )
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='会议标题（可由分身自动命名）')
    scene: Mapped[str] = mapped_column(
        sa.String(32),
        default='meeting',
        comment='场景 (meeting:会议:blue/interview:面试:violet/call:通话:green/lecture:课堂:amber)',
    )
    response_mode: Mapped[str] = mapped_column(
        sa.String(16),
        default='manual',
        comment='应答模式 (auto:自动应答:green/manual:点了才答:blue/transcribe_only:仅转写:gray)',
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='active', comment='状态 (active:进行中:green/ended:已结束:gray)'
    )
    source_config: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='采集快照 JSON（system_audio/mic/devices/stealth 三档开关）'
    )
    projection_conversation_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='完成投影到的主 IM 会话 id（null=未投影）'
    )
    projection_message_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='投影的那条卡片消息 id（点击→导航回工作会话详情）'
    )
    started_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='会议开始时间')
    ended_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='会议结束时间')
