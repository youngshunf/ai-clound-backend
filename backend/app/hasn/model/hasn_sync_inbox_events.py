from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.database.schema_names import SYNC_SCHEMA
from backend.utils.timezone import timezone


class HasnSyncInboxEvents(Base):
    """HASN 客户端上行 outbox 幂等/冲突表"""

    __tablename__ = 'hasn_sync_inbox_events'
    __table_args__ = (
        sa.UniqueConstraint(
            'owner_id',
            'node_id',
            'client_event_id',
            name='uq_hasn_sync_inbox_client_event',
        ),
        {'schema': SYNC_SCHEMA},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    client_event_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='客户端事件 ID')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件所属 Owner hasn_id')
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='事件主体 hasn_id（Human 或 owned Agent）')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上报 Node ID')
    event_type: Mapped[str] = mapped_column(sa.String(50), default='', comment='事件类型 (ack:确认:green/read:已读:blue/edit:编辑:orange/recall:撤回:red/local_state:本地状态:gray)')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='客户端上行载荷（不得包含 workspace/endpoint/PID/CLI args/OAuth path）')
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='业务幂等键')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='处理状态（accepted/processing/retry/applied/dead/conflict）')
    server_revision: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='对应服务端 revision')
    conflict_reason: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='冲突原因')
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer(),
        default=0,
        server_default=sa.text('0'),
        comment='业务应用尝试次数；每次领取原子加一',
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='失败后的下次可领取时间')
    locked_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='当前领取该事件的 worker 实例 ID')
    locked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='当前 worker 租约起始时间')
    last_error: Mapped[str | None] = mapped_column(sa.Text(), default=None, comment='最近一次业务应用失败摘要')
    applied_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='业务写已提交且 sync ACK 已落库的时间')
    dead_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='重试耗尽进入 dead 状态的时间')
    received_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='服务端接收时间')
