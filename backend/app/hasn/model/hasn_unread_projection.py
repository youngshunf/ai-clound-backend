from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnUnreadProjection(Base):
    """HASN 未读投影（可重建 read model·doc16 §4.3）

    **非权威**：未读数只能由 message.conversation_seq + membership(joined/left/read_seq) + 可见性
    重算。本表是性能可选的物化投影，必须明确标记为可重建、有 reconciler 按序号重算、漂移时以
    权威序号为准，且**不再**由并发业务路径对 unread_count 做无条件读改写（那正是旧
    hasn_unread_counts 漂移的根因）。
    """

    __tablename__ = 'hasn_unread_projection'

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='所属会话 ID')
    member_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员 hasn_id')
    unread_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='未读数（可重建·漂移时以 message/membership/read_seq 为准）')
    computed_at_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本投影计算时的会话 current_seq（判是否需要重算）')
