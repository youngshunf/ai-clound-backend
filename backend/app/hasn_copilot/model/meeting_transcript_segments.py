import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase


class MeetingTranscriptSegments(CopilotBase):
    """会议转写记录定稿分段（云端权威快照）

    定稿分段按 (meeting_id, record_version, seq) 幂等上推——同版本同序号覆盖，
    避免 daemon 重复上推产生重复分段；meeting_id 逻辑指向 meetings.id。
    """

    __tablename__ = 'meeting_transcript_segments'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='分段主键 ID（UUID）'
    )
    meeting_id: Mapped[str | UUID] = mapped_column(
        sa.UUID(), default=None, comment='所属会议 ID（逻辑指向 hasn_copilot.meetings.id）'
    )
    record_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='所属转写记录定稿版本号')
    seq: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='分段序号（本 record_version 内递增，幂等键之一）'
    )
    track: Mapped[str | None] = mapped_column(
        sa.String(16), default=None, comment='采集轨 (system:系统声:blue/mic:麦克风:green)'
    )
    speaker_label: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='说话人标签（如 说话人1）')
    speaker_source: Mapped[str | None] = mapped_column(
        sa.String(16), default=None, comment='说话人证据层级（推断来源，如 vad/cluster/manual）'
    )
    text: Mapped[str] = mapped_column(sa.Text(), default='', comment='定稿文本')
    started_ms: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='起始时间（相对会议起点毫秒）')
    ended_ms: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='结束时间（相对会议起点毫秒）')
