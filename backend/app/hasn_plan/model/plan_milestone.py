from datetime import date

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import id_key


class PlanMilestone(PlanBase):
    """里程碑（n:1 plan）"""

    __tablename__ = 'plan_milestone'

    id: Mapped[id_key] = mapped_column(init=False)
    plan_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    due_date: Mapped[date | None] = mapped_column(sa.DATE(), default=None, comment=None)
    done: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment=None)
    # 轻状态三态（PLAN-LOOP §3.2）：done bool 保留兼容读，写侧由 status 派生（status='done' ↔ done=true）。
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='planned',
        comment='状态 (planned:未开始:gray/doing:进行中:amber/done:已完成:green)',
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
