from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class UserCreditBalance(Base):
    """用户积分余额"""

    __tablename__ = 'user_credit_balance'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    credit_type: Mapped[str] = mapped_column(
        sa.String(32),
        default='',
        comment='积分类型 (monthly:月度赠送:blue/purchased:购买积分:green/bonus:活动赠送:orange)',
    )
    original_amount: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='原始积分数量')
    used_amount: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=Decimal('0'), comment='已使用积分')
    remaining_amount: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='剩余积分数量')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间')
    granted_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='发放时间')
    source_type: Mapped[str] = mapped_column(
        sa.String(32),
        default='',
        comment='来源类型 (subscription_grant:订阅发放/subscription_upgrade:升级发放/purchase:购买/bonus:赠送/refund:退款返还)',
    )
    source_reference_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联订单号')
    description: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='描述')
