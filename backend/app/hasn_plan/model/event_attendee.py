from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_plan.model._base import APP_SCHEMA, PlanBase
from backend.common.model import TimeZone, id_key


class EventAttendee(PlanBase):
    """企业事件参会人（RSVP）；event 必 enterprise_id IS NOT NULL（不变量 #4）"""

    __tablename__ = 'event_attendee'

    # 覆写 PlanBase 的 dict __table_args__：显式声明 UNIQUE + 索引，令 metadata.create_all 与 SQL 迁移一致
    # （否则 create_all 抢先建表会漏掉 UNIQUE，见迁移 DO 块补齐说明）。
    __table_args__ = (
        sa.UniqueConstraint('event_id', 'attendee_hasn_id', name='uq_event_attendee'),
        sa.Index('idx_event_attendee_who', 'attendee_hasn_id', 'enterprise_id'),
        {'comment': '企业事件参会人（RSVP）；event 必 enterprise_id IS NOT NULL（不变量 #4）', 'schema': APP_SCHEMA},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    event_id: Mapped[int] = mapped_column(
        sa.BIGINT(), sa.ForeignKey('hasn_plan.event.id', ondelete='CASCADE'), default=0, comment='所属企业事件'
    )
    enterprise_id: Mapped[int] = mapped_column(
        sa.BIGINT(), default=0, comment='冗余企业 id（恒前置查询用；逻辑引用 public.hasn_enterprise.id）'
    )
    attendee_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='参会人 HASN id（human owner）')
    # 说明：完整含配色的字典口径（key:label:color）以 SQL 迁移 COMMENT ON 为权威（webui 据此配色）；
    # 此处模型 comment 仅供 create_all 从零建表用，去配色以控行宽，二者语义一致。
    role: Mapped[str] = mapped_column(
        sa.String(16), default='required', comment='角色 (organizer:组织者/required:必到/optional:可选)'
    )
    rsvp: Mapped[str] = mapped_column(
        sa.String(16),
        default='none',
        comment='回执 (none:未回复/accepted:接受/declined:拒绝/tentative:待定)',
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='RSVP 回复时间（NULL=尚未回复）'
    )
