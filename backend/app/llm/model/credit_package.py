from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class CreditPackage(Base):
    """积分包配置表 - 定义可购买的积分包"""

    __tablename__ = 'credit_package'

    id: Mapped[id_key] = mapped_column(init=False)
    package_name: Mapped[str] = mapped_column(sa.String(64), default='', comment='积分包名称')
    credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='基础积分数量')
    price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='价格 (USD)')
    bonus_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='额外赠送积分')
    description: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='描述')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
