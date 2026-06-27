from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key, TimeZone
from backend.utils.timezone import timezone


class LeadRef(HasnGrowthAppBase):
    """用户↔线索引用（统一线索池：用户引用池中线索·用户级状态落本表不污染池行）"""

    __tablename__ = 'lead_ref'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger(), default=0, comment='引用线索的用户 ID（owner）')
    lead_contact_id: Mapped[int] = mapped_column(
        sa.BigInteger(), default=0, comment='被引用的线索池行 ID（hasn_growth.contact.id）'
    )
    source: Mapped[str] = mapped_column(
        sa.String(16),
        default='request',
        comment='来源 (request:请求匹配:blue/manual:手动登记:cyan/collect:分身采集:green/backfill:缺口补爬:orange)',
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='new', comment='状态 (new:新线索:blue/qualified:已晋级:green/dismissed:已忽略:gray)'
    )
    dismiss_reason: Mapped[str | None] = mapped_column(
        sa.String(255), default=None, comment='忽略原因（status=dismissed 时记录）'
    )
    note: Mapped[str | None] = mapped_column(sa.Text(), default=None, comment='用户对该线索的备注')
    acquired_at: Mapped[datetime] = mapped_column(
        TimeZone, default_factory=timezone.now, comment='获得该线索引用的时间'
    )
