import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase


class CopilotPreference(CopilotBase):
    """会议副驾 owner 级偏好（单行 per owner，云端权威）"""

    __tablename__ = 'copilot_preference'

    # owner_hasn_id 即主键（单行 per owner；无自增 id 列，与 DDL 一致）
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64),
        primary_key=True,
        sort_order=-999,
        comment='owner HASN ID（主键，单行 per owner；引用 public.hasn_humans）',
    )
    default_agent_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='默认协作分身（首次绑定写入；新会话默认用它，§8.5）'
    )
    default_response_mode: Mapped[str] = mapped_column(
        sa.String(16),
        default='manual',
        comment='默认应答模式 (auto:自动应答:green/manual:点了才答:blue/transcribe_only:仅转写:gray)',
    )
    auto_summary: Mapped[bool] = mapped_column(sa.Boolean(), default=True, comment='会后是否自动生成纪要产物')
