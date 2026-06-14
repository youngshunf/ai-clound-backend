from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class SubscriptionTier(BillingBase):
    """订阅等级配置表"""

    __tablename__ = 'subscription_tier'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    tier_name: Mapped[str] = mapped_column(sa.String(32), default='', comment='等级标识 (free:免费版/basic:基础版/pro:专业版/enterprise:企业版)')
    display_name: Mapped[str] = mapped_column(sa.String(64), default='', comment='显示名称')
    monthly_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='每月赠送积分')
    monthly_price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='月费')
    yearly_price: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, nullable=True, comment='年费价格')
    yearly_discount: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, nullable=True, comment='年费折扣 (如 0.8 表示8折)')
    max_agents: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment='Agent 最大数量（默认值，创建订阅时复制到 user_subscription）')
    features: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='功能特性')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
