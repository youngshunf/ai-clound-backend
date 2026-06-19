from datetime import date

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import id_key


class HabitCheckin(PlanBase):
    """习惯打卡（一天一卡，UNIQUE(habit_id,checkin_date)）"""

    __tablename__ = 'habit_checkin'

    id: Mapped[id_key] = mapped_column(init=False)
    habit_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    checkin_date: Mapped[date] = mapped_column(sa.DATE(), default_factory=date.today, comment=None)
    note: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
