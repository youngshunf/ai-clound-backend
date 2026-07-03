from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class UserSubscription(BillingBase):
    """用户订阅表"""

    __tablename__ = 'user_subscription'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    tier: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅等级 (free:免费版/basic:基础版/pro:专业版/enterprise:企业版)')
    subscription_type: Mapped[str] = mapped_column(sa.String(16), default='monthly', comment='订阅类型 (monthly:月度/yearly:年度)')
    monthly_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='每月积分配额')
    current_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='当前剩余积分')
    used_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='本周期已使用积分')
    purchased_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='购买的额外积分')
    billing_cycle_start: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期开始时间')
    billing_cycle_end: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期结束时间')
    subscription_start_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='订阅开始时间')
    subscription_end_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='订阅结束时间')
    next_grant_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='下次赠送积分时间 (年度订阅专用)')
    status: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅状态 (active:激活/expired:已过期/cancelled:已取消)')
    auto_renew: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否自动续费')
    max_agents: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment='Agent 最大数量（跨服务器总计）')
