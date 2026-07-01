from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnAppSeat(Base):
    """企业应用命名席位（云端权威）

    企业购买某 app N 席后，owner/admin 把席位指派给具体成员（一席一行，指派/回收随成员）。
    席位挂在 entitlement「套餐」行下（hasn_app_entitlement.seats_total 为总数）。
    设计事实源：docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6.1
    """

    __tablename__ = 'hasn_app_seat'

    id: Mapped[id_key] = mapped_column(init=False)
    entitlement_id: Mapped[int] = mapped_column(
        sa.BigInteger(), default=0, comment='所属企业权益「套餐」行 ID（hasn_app_entitlement.id）'
    )
    enterprise_id: Mapped[int] = mapped_column(sa.BigInteger(), default=0, comment='企业 ID')
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用唯一标识')
    member_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被指派席位的成员 owner hasn_id')
    assigned_by: Mapped[str] = mapped_column(sa.String(40), default='', comment='指派人 owner/admin hasn_id')
    status: Mapped[str] = mapped_column(
        sa.String(16), default='assigned', comment='席位状态 (assigned:已指派:green/released:已回收:gray)'
    )
    assigned_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='指派时间')
    released_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='回收时间（released 时）')
