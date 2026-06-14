from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class CreditTransaction(BillingBase):
    """积分交易记录表"""

    __tablename__ = 'credit_transaction'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    transaction_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='交易类型 (usage:使用/purchase:购买/refund:退款/monthly_grant:月度赠送/bonus:奖励)')
    credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='积分变动数量')
    balance_before: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='交易前余额')
    balance_after: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='交易后余额')
    reference_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联 ID')
    reference_type: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='关联类型 (llm_usage:LLM调用/payment:支付/system:系统)')
    description: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='交易描述')
    extra_data: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='扩展数据')
