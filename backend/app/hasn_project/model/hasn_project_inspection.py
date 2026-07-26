from datetime import datetime
import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_project.model._base import HasnProjectAppBase
from backend.common.model import UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnProjectInspection(HasnProjectAppBase):
    """平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）"""

    __tablename__ = 'hasn_project_inspection'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='巡检建议云端权威 UUID'
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键）')
    project_id: Mapped[UUID] = mapped_column(sa.UUID(), default=None, comment='所属平台项目云端权威 UUID')
    agent_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发布巡检建议的项目经理分身 HASN ID')
    fingerprint: Mapped[str] = mapped_column(sa.String(128), default='', comment='建议幂等指纹（同 owner/项目重放不重复插入）')
    suggestion: Mapped[str] = mapped_column(UniversalText, default='', comment='给主人展示的巡检建议正文')
    suggested_instruction: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='按建议派发时预填给分身的执行指令')
    status: Mapped[str] = mapped_column(sa.String(16), default='unread', comment='状态 (unread:未读:violet/dispatched:已派发:blue/dismissed:已忽略:gray/reminded:已提醒:amber)')
    inspected_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='分身完成本次巡检的时间')
    handled_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='主人处理建议的时间')
    work_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='按建议派发后回填的工作会话 ID（逻辑引用 public.hasn_sessions）')
    plan_todo_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='提醒今晚后回填的计划待办 ID（逻辑引用 hasn_plan.todo）')
