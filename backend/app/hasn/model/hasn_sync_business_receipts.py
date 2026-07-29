from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnSyncBusinessReceipts(Base):
    """sync inbox 业务应用的事务内幂等回执"""

    __tablename__ = 'hasn_sync_business_receipts'
    __table_args__ = (
        sa.UniqueConstraint(
            'idempotency_key',
            name='uq_hasn_sync_business_receipt_key',
        ),
        sa.UniqueConstraint(
            'owner_id',
            'node_id',
            'client_event_id',
            name='uq_hasn_sync_business_receipt_event',
        ),
    )

    id: Mapped[id_key] = mapped_column(init=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(256), default='', comment='worker 派生的稳定幂等键')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人隔离键')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上报节点 ID')
    client_event_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='客户端事件 ID')
    event_type: Mapped[str] = mapped_column(sa.String(80), default='', comment='已应用的业务事件类型')
    applied_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='业务事务提交时间')
