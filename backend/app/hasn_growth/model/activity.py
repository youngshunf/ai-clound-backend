from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class Activity(HasnGrowthAppBase):
    """获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）"""

    __tablename__ = 'activity'

    id: Mapped[id_key] = mapped_column(init=False)
    customer_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    opportunity_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    kind: Mapped[str] = mapped_column(sa.String(24), default='', comment='类型 (outreach:触达:blue/reply:回复:green/stage_change:阶段变更:orange/task_run:任务:cyan/note:备注:gray/call:电话:purple/meeting:会议:purple/qualify:晋级:blue/close:成交:green)')
    content: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    actor_kind: Mapped[str | None] = mapped_column(sa.String(8), default=None, comment='执行者 (owner:主人:blue/agent:分身:violet)')
    actor_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    ref_table: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment=None)
    ref_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    occurred_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    # 企业化双模归属（GE1，设计 v3 §6.7）：承自客户，便于企业全量时间线聚合。
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）')
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式，承自客户）')
