from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class ModelCreditRate(BillingBase):
    """模型积分费率表"""

    __tablename__ = 'model_credit_rate'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    model_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='模型 ID')
    base_credit_per_1k_tokens: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='基准积分费率')
    input_multiplier: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='输入倍率')
    output_multiplier: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='输出倍率')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
