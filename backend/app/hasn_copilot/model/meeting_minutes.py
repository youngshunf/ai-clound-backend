import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase


class MeetingMinutes(CopilotBase):
    """会议纪要正文版本化（云端权威）

    纪要按 version 版本化——分身重写纪要即写新版本，meetings.minutes_version 指向当前版本；
    幂等键 (meeting_id, version) 保证同版本重复写入覆盖不重复。meeting_id 逻辑指向 meetings.id。
    """

    __tablename__ = 'meeting_minutes'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='纪要版本主键 ID（UUID）'
    )
    meeting_id: Mapped[str | UUID] = mapped_column(
        sa.UUID(), default=None, comment='所属会议 ID（逻辑指向 hasn_copilot.meetings.id）'
    )
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='纪要版本号（幂等键之一）')
    body_md: Mapped[str] = mapped_column(sa.Text(), default='', comment='纪要正文（Markdown）')
    record_view_version: Mapped[int | None] = mapped_column(
        sa.INTEGER(), default=None, comment='生成此纪要时依据的转写记录视图版本（record_version 快照）'
    )
    summary_turn_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='生成此纪要的工作会话轮次 id（溯源）'
    )
