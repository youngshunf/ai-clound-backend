from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class Event(PlanBase):
    """日程/时间块（日历单元；flex 块由 todo_id 权威关联，1:0..1）"""

    __tablename__ = 'event'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='所属企业 id（NULL=个人事件；逻辑引用 public.hasn_enterprise，无硬 FK）'
    )
    dept_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='所属部门 id（NULL=不限部门）')
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment=None)
    kind: Mapped[str] = mapped_column(
        sa.String(8), default='fixed', comment='块类型 (fixed:固定:gray/flex:弹性:violet/break:休息:slate)'
    )
    actor: Mapped[str | None] = mapped_column(
        sa.String(8), default=None, comment='执行归属 (owner:亲为:violet/collab:协作:amber/attend:出席:blue)'
    )
    start_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    end_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    locked: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='flex 块被拖/锁后置 true，重排不动（§8.1 不变量）'
    )
    all_day: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment=None)
    todo_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='flex 块实现的待办（权威关联，1:0..1；fixed/break 为 NULL）'
    )
    recurrence: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    schedule_reason: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='为什么排在这里的推理串（信任机制 §8.4）'
    )
    source: Mapped[str] = mapped_column(
        sa.String(16),
        default='manual',
        comment='来源 (chat:对话:cyan/manual:手动:gray/capture:捕获:blue/decompose:分解:violet/oa_meeting:会议室预定:blue/oa_interview:面试:violet)',  # noqa: E501
    )
    visibility: Mapped[str] = mapped_column(
        sa.String(16),
        default='private',
        comment='企业事件可见性 (private:仅参与者+被授权:gray/public:企业公开:green)（个人事件恒 private，不生效）',
    )
    origin_ref: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='外部来源锚 (OA→plan：oa:room_booking:<id>/oa:interview:<id>)；日历权威在 plan（[04] §6.3）',
    )
