import uuid

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase
from backend.common.model import TimeZone


class MeetingEnhancementRevisions(CopilotBase):
    """会议会后增强候选 revision（云端权威，含淘汰审计元数据）"""

    __tablename__ = 'meeting_enhancement_revisions'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(),
        primary_key=True,
        default_factory=uuid.uuid4,
        init=False,
        comment='候选权威 server_id',
    )
    meeting_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='所属会议云端权威 ID')
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归属主人 HASN ID（冗余隔离键，所有查询强制带）'
    )
    operation_id: Mapped[str] = mapped_column(
        sa.String(128), default='', comment='daemon 稳定增强操作 ID（同会议幂等）'
    )
    revision_number: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='会议内单调递增候选序号')
    supersedes: Mapped[str | UUID] = mapped_column(
        sa.UUID(), default=None, comment='来源 revision 的云端权威 ID（原始实时稿或既有候选）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(32),
        default='pending_confirmation',
        comment=(
            '状态 (pending_confirmation:待主人确认:amber/accepted:已接受:green/'
            'rejected:已拒绝:gray/superseded:已被新候选替换:blue/'
            'evicted:已按保留策略淘汰:red)'
        ),
    )
    source_record_version: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='生成候选所依据的原始实时稿 record_version'
    )
    transcript_json: Mapped[dict | list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='候选转写结果；淘汰后清空，仅保留审计元数据'
    )
    speaker_annotations_json: Mapped[dict | list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='候选说话人标注结果；可选输出失败时可为空'
    )
    alignment_json: Mapped[dict | list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='候选强制对齐结果；可选输出失败时可为空'
    )
    model_run_id: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='本次联合或组合推理的 model_run_id'
    )
    model_evidence_json: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='模型、组件版本、能力结果和错误的结构化证据'
    )
    created_by_agent_hasn_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='参与创建候选的分身 HASN ID；纯语音引擎写入时为空'
    )
    work_session_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='分身参与时绑定的工作会话 ID'
    )
    replaced_by: Mapped[str | UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='替换当前待确认候选的新候选 server_id'
    )
    decision_reason: Mapped[str | None] = mapped_column(
        sa.String(256), default=None, comment='主人拒绝或系统替换时的稳定原因'
    )
    decided_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='主人接受或拒绝时间')
    eviction_reason: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='淘汰原因；首版固定 retention_limit'
    )
    evicted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='按保留策略淘汰时间')
