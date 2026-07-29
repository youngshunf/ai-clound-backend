from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key
from backend.database.schema_names import IM_SCHEMA


class HasnImHistorySnapshotMessages(Base):
    """跨设备历史快照的不可变消息投影"""

    __tablename__ = 'hasn_im_history_snapshot_messages'
    __table_args__ = (
        sa.UniqueConstraint(
            'snapshot_id',
            'item_index',
            name='uq_hasn_im_history_snapshot_messages_index',
        ),
        sa.UniqueConstraint(
            'snapshot_id',
            'message_id',
            name='uq_hasn_im_history_snapshot_messages_source',
        ),
        sa.CheckConstraint(
            'item_index > 0',
            name='ck_hasn_im_history_snapshot_messages_index',
        ),
        sa.CheckConstraint(
            'message_id > 0',
            name='ck_hasn_im_history_snapshot_messages_message_id',
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
    message_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='权威消息 ID')
    payload: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='daemon 可直接消费的消息投影 JSON'
    )
