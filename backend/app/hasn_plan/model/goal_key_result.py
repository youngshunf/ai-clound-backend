from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import id_key


class GoalKeyResult(PlanBase):
    """关键结果（KR，n:1 goal）"""

    __tablename__ = 'goal_key_result'

    id: Mapped[id_key] = mapped_column(init=False)
    goal_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    metric: Mapped[str] = mapped_column(sa.String(255), default='', comment='指标名')
    unit: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    current_value: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=0, comment=None)
    target_value: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=0, comment=None)
    direction: Mapped[str] = mapped_column(
        sa.String(8), default='up', comment='方向 (up:越高越好:green/down:越低越好:blue)'
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
