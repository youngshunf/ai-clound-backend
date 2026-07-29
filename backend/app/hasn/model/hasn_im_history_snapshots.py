import uuid

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone
from backend.database.schema_names import IM_SCHEMA
from backend.utils.timezone import timezone


class HasnImHistorySnapshots(Base):
    """跨设备会话与消息历史物化快照"""

    __tablename__ = 'hasn_im_history_snapshots'
    __table_args__ = (
        sa.CheckConstraint(
            'head_revision >= 0',
            name='ck_hasn_im_history_snapshots_head_revision',
        ),
        sa.CheckConstraint(
            'message_upper_bound >= 0',
            name='ck_hasn_im_history_snapshots_message_upper_bound',
        ),
        sa.CheckConstraint(
            'conversation_count >= 0 AND message_count >= 0',
            name='ck_hasn_im_history_snapshots_counts',
        ),
        {'schema': IM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(),
        primary_key=True,
        default_factory=uuid.uuid4,
        init=False,
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='快照所属主人 HASN ID')
    identity_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(),
        default_factory=list,
        comment='建立快照时主人本人及名下分身 HASN ID 集合',
    )
    head_revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='建立快照前读取的主人增量同步流头')
    message_upper_bound: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='物化消息中的最大权威消息 ID')
    conversation_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='物化会话数量')
    message_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='物化消息数量')
    history_complete: Mapped[bool] = mapped_column(
        sa.BOOLEAN(),
        default=False,
        comment='所有会话均已证明历史完整',
    )
    expires_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='快照服务端失效时间')
