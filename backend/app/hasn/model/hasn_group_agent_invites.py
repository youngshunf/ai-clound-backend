from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnGroupAgentInvites(Base):
    """HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）"""

    __tablename__ = 'hasn_group_agent_invites'

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='群会话 ID（关联 hasn_conversations）')
    group_id: Mapped[str] = mapped_column(sa.String(20), default='', comment='群组公开标识（g:NNNNNN）')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被邀请的分身 hasn_id')
    agent_owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='分身主人 hasn_id（冗余列，便于按主人查询/判权）')
    inviter_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起人 hasn_id')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待确认:orange/accepted:已同意:green/declined:已拒绝:red/expired:已过期:gray/cancelled:已取消:gray)')
    resolved_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='处理时间（accept/decline/expire/cancel）')
