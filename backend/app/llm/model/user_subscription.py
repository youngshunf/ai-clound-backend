from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class UserSubscription(Base):
    """用户订阅表 - 管理用户的订阅等级和积分余额"""

    __tablename__ = 'user_subscription'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID (外键 sys_user.id)')
    tier: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅等级: free/basic/pro/enterprise')
    monthly_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='每月积分配额')
    current_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='当前剩余积分 (月度积分 + 购买积分)')
    used_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='本周期已使用积分')
    purchased_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='购买的额外积分 (不会过期)')
    billing_cycle_start: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期开始时间 (按用户注册日期)')
    billing_cycle_end: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期结束时间 (30天后)')
    status: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅状态: active/expired/cancelled')
    auto_renew: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否自动续费')
