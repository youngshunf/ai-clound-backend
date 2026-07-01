from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnAppEntitlement(Base):
    """AI-Native 应用权益（云端权威）"""

    __tablename__ = 'hasn_app_entitlement'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用唯一标识')
    subject_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='权益主体 (owner:个人:blue/enterprise:企业:purple)')
    subject_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主体 ID（owner=hasn_id / enterprise=enterprise_id）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='权益来源 (purchase:购买:green/trial:试用:orange/admin_grant:管理员授予:blue)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='权益状态 (active:生效:green/expired:已过期:gray/revoked:已撤销:red)')
    order_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联支付订单号（purchase 时）')
    seats_total: Mapped[int | None] = mapped_column(
        sa.Integer(),
        default=None,
        comment='席位总数(subject_type=enterprise 席位制有效; owner 恒 null)（doc04 §6.1）',
    )
    granted_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='授予时间')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间（null=永久买断）')
