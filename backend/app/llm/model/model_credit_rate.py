from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class ModelCreditRate(Base):
    """模型积分费率表 - 定义不同模型的积分消耗规则"""

    __tablename__ = 'model_credit_rate'

    id: Mapped[id_key] = mapped_column(init=False)
    model_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='模型 ID (外键 llm_model_config.id)')
    base_credit_per_1k_tokens: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='基准积分费率 (每 1K tokens)')
    input_multiplier: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='输入 tokens 倍率')
    output_multiplier: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='输出 tokens 倍率')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
