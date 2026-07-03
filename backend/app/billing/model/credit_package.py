from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class CreditPackage(BillingBase):
    """积分包配置表"""

    __tablename__ = 'credit_package'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    package_name: Mapped[str] = mapped_column(sa.String(64), default='', comment='积分包名称')
    credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='基础积分数量')
    price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='价格')
    bonus_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='额外赠送积分')
    description: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='描述')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
