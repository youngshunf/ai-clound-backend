from datetime import datetime, time

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import PlanBase
from backend.common.model import id_key


class Preference(PlanBase):
    """排程偏好（owner 单例，一主人一行；id PK + UNIQUE(owner_hasn_id) 保不变量）"""

    __tablename__ = 'preference'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    working_hours: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='每日工作时段')
    energy_profile: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    buffer_minutes: Mapped[int] = mapped_column(sa.SMALLINT(), default=10, comment=None)
    no_schedule_windows: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='不可排时段')
    default_autonomy_by_risk: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    briefing_morning_time: Mapped[time | None] = mapped_column(sa.TIME(), default=None, comment=None)
    briefing_evening_time: Mapped[time | None] = mapped_column(sa.TIME(), default=None, comment=None)
    timezone: Mapped[str] = mapped_column(sa.String(48), default='Asia/Shanghai', comment=None)
    # KNOWU §7：主动规划闭环幂等标记（首次 all_sufficient 原子认领一次，跨设备持久）
    proactive_planned: Mapped[bool] = mapped_column(
        sa.Boolean(), default=False, comment='主动规划闭环是否已触发（首次 all_sufficient 认领一次）'
    )
    # KNOWU「每日关注·了解主人」节奏闸（简报每天跑，采访/成长会话不能每天派）：
    # 记「上次派采访/成长会话的时间」，配「超冷却期才可重新认领」的周期性 claim（默认 7 天），
    # 避免每天打扰主人；NULL=从未派过。
    last_onboarding_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None, comment='上次派「了解主人」采访会话时间（周期闸，NULL=从未）'
    )
    last_growth_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None, comment='上次派「成长复盘/主动规划」会话时间（周期闸，NULL=从未）'
    )
