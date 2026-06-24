from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnSuppressedMessages(Base):
    """HASN Runtime 抑制箱 / owner 可拉取消息表"""

    __tablename__ = 'hasn_suppressed_messages'

    id: Mapped[id_key] = mapped_column(init=False)
    message_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='已入 inbox 的消息 ID')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner hasn_id')
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被抑制的 Agent/Human inbox 主体 hasn_id')
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='原会话 ID（不得因 Runtime 状态分裂会话）')
    suppress_reason: Mapped[str] = mapped_column(sa.String(40), default='', comment='抑制原因 (runtime_unavailable:Runtime不可用:orange/adapter_missing:Adapter缺失:red/handle_unavailable:Handle不可用:orange/owner_confirmation_required:需Owner确认:purple/policy_suppressed:策略抑制:gray/social_disabled:未开启社交:gray/permission_denied:权限不足:red/agent_frozen:分身已冻结:orange/abuse_restricted:风控限制:red/manual_only:待手动放行:purple)')
    dispatch_status: Mapped[str] = mapped_column(sa.String(30), default='', comment='Runtime 调度状态 (runtime_unavailable:Runtime不可用:orange/dispatch_failed:派发失败:red/suppressed_by_policy:策略抑制:purple/pending_runtime:等待Runtime:blue)')
    policy_snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='策略快照摘要')
    runtime_summary: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='Runtime 脱敏摘要；禁止 workspace/endpoint/PID/CLI args/OAuth path')
    visible_to_owner: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='Owner 多端是否可见')
    resolved_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='抑制状态解除时间')
