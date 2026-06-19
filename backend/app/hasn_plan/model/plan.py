import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import UniversalText, id_key


class Plan(PlanBase):
    """计划/项目（目标的中期拆解，含里程碑/阶段；协作型产物）"""

    __tablename__ = 'plan'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    goal_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='active',
        comment='状态 (active:进行中:blue/paused:暂停:orange/done:已完成:green/archived:已归档:gray)',
    )
    bound_agent_id: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='AppCollab 平台标准列：协作分身 agent hasn id（逻辑引用，doc21 §4.1）'
    )
    active_work_session_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='AppCollab 快路径：当前/最近工作会话 id（逻辑引用，权威经 origin_ref 反查，doc21 §4.2）',
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
