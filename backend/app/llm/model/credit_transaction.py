from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class CreditTransaction(Base):
    """积分交易记录表 - 审计所有积分变动"""

    __tablename__ = 'credit_transaction'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    transaction_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='交易类型: usage/purchase/refund/monthly_grant/bonus/adjustment')
    credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='积分变动数量 (正数=增加, 负数=扣除)')
    balance_before: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='交易前余额')
    balance_after: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='交易后余额')
    reference_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联 ID (如 usage_log.request_id, payment.order_id)')
    reference_type: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='关联类型: llm_usage/payment/system/manual')
    description: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='交易描述')
    metadata_: Mapped[dict | None] = mapped_column('metadata', postgresql.JSONB(), default=None, comment='扩展元数据 (JSON)')
