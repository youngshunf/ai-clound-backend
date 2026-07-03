from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_diag.model._base import HasnDiagAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class ErrorIssue(HasnDiagAppBase):
    """错误问题（fingerprint 聚合·运维处理与状态单元·长期保留知识资产）"""

    __tablename__ = 'error_issue'

    id: Mapped[id_key] = mapped_column(init=False)
    fingerprint: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归类键（对应 error_report.fingerprint，唯一）'
    )
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='归一化摘要（首条 occurrence 派生）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源（该类首见来源）')
    severity: Mapped[str] = mapped_column(sa.String(16), default='', comment='该类最高严重度 (critical/error/warn)')
    status: Mapped[str] = mapped_column(
        sa.String(16), default='open', comment='状态 (open/investigating/resolved/skipped/wontfix)'
    )
    occurrence_count: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='累计次数（含 suppressed_count）')
    affected_owner_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='受影响主人数（去重累加）')
    affected_node_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='受影响设备数（去重累加）')
    first_seen_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='首次发生时刻')
    last_seen_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='末次发生时刻')
    resolution_type: Mapped[str | None] = mapped_column(
        sa.String(24), default=None, comment='处理方式 (code_fix/config_fix/duplicate/...)'
    )
    resolution_note: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='怎么解决/为何跳过（结案必填）'
    )
    duplicate_of_fingerprint: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='duplicate 时归并目标 fingerprint'
    )
    fixed_in_version: Mapped[str | None] = mapped_column(
        sa.String(32), default=None, comment='code_fix 修复版本（版本感知重开判据）'
    )
    snooze_until: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='skipped 的 snooze 到期时刻（空=无限期）'
    )
    issue_url: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='关联 GitHub issue')
    pr_url: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='关联 PR')
    resolved_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='处理分身 hasn_id')
    resolved_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='结案时刻')
