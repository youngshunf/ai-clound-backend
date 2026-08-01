from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key
from backend.database.schema_names import IM_SCHEMA


class HasnImHistorySnapshotConversations(Base):
    """跨设备历史快照的不可变会话投影"""

    __tablename__ = 'hasn_im_history_snapshot_conversations'
    __table_args__ = (
        sa.UniqueConstraint(
            'snapshot_id',
            'item_index',
            name='uq_hasn_im_history_snapshot_conversations_index',
        ),
        sa.UniqueConstraint(
            'snapshot_id',
            'conversation_id',
            name='uq_hasn_im_history_snapshot_conversations_source',
        ),
        sa.CheckConstraint(
            'item_index > 0',
            name='ck_hasn_im_history_snapshot_conversations_index',
        ),
        {'schema': IM_SCHEMA},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    snapshot_id: Mapped[UUID] = mapped_column(
        sa.UUID(),
        sa.ForeignKey(
            f'{IM_SCHEMA}.hasn_im_history_snapshots.id',
            ondelete='CASCADE',
        ),
        default=None,
        comment='所属物化快照 ID',
    )
    item_index: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='快照内稳定分页序号')
    conversation_id: Mapped[UUID] = mapped_column(
        sa.UUID(),
        default=None,
        comment='权威会话 UUID',
    )
    payload: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='daemon 可直接消费的会话投影 JSON'
    )
