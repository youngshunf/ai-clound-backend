import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import id_key


class Habit(PlanBase):
    """习惯/例程（周期性待办，打卡积累喂给目标）"""

    __tablename__ = 'habit'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='所属企业 id（NULL=个人习惯）；首期不 surface（PE-D1）'
    )
    dept_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='所属部门 id（NULL=不限；首期不 surface PE-D1）'
    )
    goal_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    cadence: Mapped[str] = mapped_column(
        sa.String(16), default='daily', comment='节奏 (daily:每日:green/weekly_n:每周N次:blue)'
    )
    target_count: Mapped[int] = mapped_column(sa.SMALLINT(), default=1, comment=None)
    energy: Mapped[str | None] = mapped_column(
        sa.String(8), default=None, comment='脑力档 (high:高脑力:violet/low:低脑力:gray)'
    )
    preferred_window: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    streak: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    best_streak: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16), default='active', comment='状态 (active:进行中:green/paused:暂停:orange/archived:归档:gray)'
    )
