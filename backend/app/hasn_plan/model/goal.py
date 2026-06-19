from datetime import date

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import UniversalText, id_key


class Goal(PlanBase):
    """目标（北极星，长期愿景+期限+关键结果）"""

    __tablename__ = 'goal'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 HASN id（owner 隔离键）')
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    why: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='动机')
    category: Mapped[str | None] = mapped_column(
        sa.String(32),
        default=None,
        comment='领域分类 (health:健康:green/career:事业:blue/learning:学习:violet/finance:财务:amber/relationship:关系:pink/life:生活:cyan/other:其它:gray)',  # noqa: E501
    )
    target_date: Mapped[date | None] = mapped_column(sa.DATE(), default=None, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='active',
        comment='状态 (active:进行中:blue/paused:暂停:orange/done:已达成:green/archived:已归档:gray)',
    )
    progress_pct: Mapped[int] = mapped_column(
        sa.SMALLINT(), default=0, comment='进度（派生缓存，由 KR/里程碑/待办完成率计算，不接受前端直填）'
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
