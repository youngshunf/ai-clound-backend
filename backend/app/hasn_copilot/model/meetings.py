import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_copilot.model._base import CopilotBase


class Meetings(CopilotBase):
    """会议副驾会议主档（云端权威结果容器）

    权威 id（UUID）即 hasn://meeting/{id} 的 {id} 段——daemon 深链/打开依据；
    过程留本机（工作会话 + 本地转写），结果存云端（本表 + 分段 + 纪要）。
    起止时间 started_at/ended_at/duration_ms 用 bigint（unix 秒/毫秒，daemon 收发整数），
    不用 timestamptz（daemon meetings_mirror 按 as_i64 解析）。
    """

    __tablename__ = 'meetings'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='会议权威 ID（UUID；hasn://meeting/{id}）'
    )
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归属 owner HASN ID（owner 隔离键，所有查询强制带；引用 public.hasn_humans）'
    )
    enterprise_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='所属企业 ID（团队协作预留，首发恒 NULL）'
    )
    agent_hasn_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='绑定协作分身 HASN ID（owner 名下 a_* 分身）'
    )
    session_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='处理工作会话 id（create 按 (owner,session_id) upsert）'
    )
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='采集设备节点 id')
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='会议标题（可由分身自动命名）')
    scene: Mapped[str | None] = mapped_column(
        sa.String(32),
        default=None,
        comment='场景 (meeting:会议:blue/interview:面试:violet/call:通话:green/lecture:课堂:amber)',
    )
    started_at: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='会议开始时间（unix 秒，整数）')
    ended_at: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='会议结束时间（unix 秒，整数）')
    duration_ms: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='会议时长（毫秒）')
    status: Mapped[str] = mapped_column(
        sa.String(16), default='active', comment='状态 (active:进行中:green/ended:已结束:blue/finalized:已定稿:gray)'
    )
    record_version: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='转写记录定稿版本号（segments 幂等上推时 bump）'
    )
    speaker_annotation_revision: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='说话人标注修订号（说话人定稿快照对应版本）'
    )
    participants_json: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='说话人定稿快照 JSON 数组'
    )
    minutes_state: Mapped[str] = mapped_column(
        sa.String(16),
        default='none',
        comment='纪要状态 (none:未生成:gray/queued:排队中:amber/ready:已就绪:green/failed:失败:red)',
    )
    minutes_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='纪要当前版本号（纪要写入时提升）')
    stats_json: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='会议统计 JSON 对象（时长/发言分布/要点数等）'
    )
    shared_media_json: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='升格媒体清单 JSON 数组（owner 逐件勾选升格）'
    )
