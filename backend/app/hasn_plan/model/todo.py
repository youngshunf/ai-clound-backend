from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import TimeZone, UniversalText, id_key


class Todo(PlanBase):
    """待办（最小可执行单元，本应用核心表）"""

    __tablename__ = 'todo'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    plan_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    goal_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    notes: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    actor: Mapped[str] = mapped_column(
        sa.String(8),
        default='owner',
        comment='执行归属 (owner:需你亲为:violet/collab:待你确认:amber/agent:分身自主:cyan)',
    )
    autonomy: Mapped[str] = mapped_column(
        sa.String(8), default='auto', comment='分身自主度 (auto:自动:green/review:待审:amber/ask:逐步确认:red)'
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='inbox',
        comment='状态 (inbox:收件箱:gray/todo:待办:blue/scheduled:已排期:violet/doing:进行中:cyan/waiting_review:待过目:amber/done:已完成:green/cancelled:已取消:gray)',  # noqa: E501
    )
    priority: Mapped[int] = mapped_column(sa.SMALLINT(), default=2, comment='优先级 (1:低:gray/2:中:blue/3:高:red)')
    estimated_minutes: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    energy: Mapped[str | None] = mapped_column(
        sa.String(8), default=None, comment='脑力档 (high:高脑力:violet/low:低脑力:gray)'
    )
    context_tags: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment=None)
    due_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    deadline_label: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    min_block_minutes: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    active_work_session_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='AppCollab 快路径：委托执行当前/最近工作会话（逻辑引用，权威经 origin_ref 反查）',
    )
    source: Mapped[str] = mapped_column(
        sa.String(16),
        default='manual',
        comment='来源 (chat:对话:cyan/manual:手动:gray/capture:捕获:blue/decompose:分解:violet)',
    )
    completed_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
