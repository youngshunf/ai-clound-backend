"""合并轮次（hasn_memory.merge_run，doc19 §5.5 / §5.6）。

主脑分身在自己设备上跑完一轮合并后，把整轮结果提交云端合并闸（owner advisory lock +
`owner_memory.version` CAS）。本表登记每轮的提交者、基线版本、裁决计数与结果摘要：

- §5.5 主脑单点可见：主人在记忆页看到「上次整理于 X，主脑在 <设备> 上，当前离线」；
- §5.6 拒绝可解释：`status='rejected'` 必带 `reject_reason`（非当前主脑 / 基线版本不匹配），
  主脑下轮重跑，不静默停摆。

`run_id` 即 `semantic_fact.merge_verdict_run` 指向的轮次（该列组不建外键，§3.2）。
设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md（合并轮次与节点合并规则）
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_memory.model._base import APP_SCHEMA, HasnMemoryBase
from backend.common.model import TimeZone, UniversalText
from backend.utils.timezone import timezone


class MergeRun(HasnMemoryBase):
    """HASN 记忆系统 - 合并轮次（主脑提交、云端合并闸裁定）。"""

    __tablename__ = 'merge_run'
    __table_args__ = (
        sa.CheckConstraint("status IN ('applied', 'rejected')", name='ck_merge_run_status'),
        {'comment': 'HASN 记忆系统 - 合并轮次（主脑提交、云端合并闸裁定）', 'schema': APP_SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(
        sa.String(40),
        primary_key=True,
        default='',
        comment='合并轮次 ID（主键；semantic_fact.merge_verdict_run 指向它）',
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 hasn_id（一轮合并只针对一个主人）')
    submitted_node_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='提交节点 node_id（主脑所在设备）'
    )
    submitted_agent_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='提交分身 hasn_id（主脑分身）')
    base_owner_memory_version: Mapped[int] = mapped_column(
        sa.Integer, default=0, comment='提交声明的基线 owner_memory.version（合并闸 CAS 依据）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='applied', comment='轮次结果 (applied:已应用:green/rejected:已拒绝:red)'
    )
    reject_reason: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='拒绝原因（not_master_brain / version_conflict 等；status=rejected 时必填）',
    )
    facts_judged: Mapped[int] = mapped_column(sa.Integer, default=0, comment='本轮读入裁决的活跃事实数')
    facts_merged: Mapped[int] = mapped_column(sa.Integer, default=0, comment='本轮标 merged_into 的事实数')
    facts_disputed: Mapped[int] = mapped_column(sa.Integer, default=0, comment='本轮标 disputed（待主人确认）的事实数')
    summary: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='主脑用人话写的结果摘要（面向主人，记忆页可见）'
    )
    started_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='本轮开始时间')
    finished_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='本轮结束时间（含被拒）')
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
