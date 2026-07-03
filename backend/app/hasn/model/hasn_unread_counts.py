from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnUnreadCounts(Base):
    """HASN 未读计数表"""

    __tablename__ = 'hasn_unread_counts'

    id: Mapped[id_key] = mapped_column(init=False)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='用户/Agent 的 hasn_id')
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='会话 ID')
    unread_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='未读消息数')
    last_read_msg_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='最后已读消息 ID')
