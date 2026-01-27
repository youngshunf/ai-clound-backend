from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class SubscriptionTier(Base):
    """订阅等级配置表 - 定义不同订阅等级的权益"""

    __tablename__ = 'subscription_tier'

    id: Mapped[id_key] = mapped_column(init=False)
    tier_name: Mapped[str] = mapped_column(sa.String(32), default='', comment='等级标识: free/basic/pro/enterprise')
    display_name: Mapped[str] = mapped_column(sa.String(64), default='', comment='显示名称')
    monthly_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='每月赠送积分')
    monthly_price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='月费 (USD)')
    features: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='功能特性 (JSON)')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
