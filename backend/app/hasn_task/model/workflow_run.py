"""工作流执行实例模型（hasn_task.workflow_run）。

一次 fire = 一个 workflow_run。每个节点的本次执行 = 一条 hasn_task.run
（+ workflow_run_uuid + node_key）。W5 驱动权租约 + 图快照在本表（07 §5.0/§5.4）。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §4.1。
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key

WORKFLOW_RUN_STATUS_COMMENT = (
    '状态 (running:运行中:orange/completed:已完成:green/failed:失败:red/blocked:阻塞:orange/cancelled:已取消:gray)'
)


class HasnWorkflowRun(HasnTaskAppBase):
    """工作流执行实例表"""

    __tablename__ = 'workflow_run'

    id: Mapped[id_key] = mapped_column(init=False)
    workflow_run_uuid: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='端云稳定执行实例 UUID（同步主键）'
    )
    workflow_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属工作流稳定 UUID')
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属 owner')
    scheduled_fire_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='触发时刻')
    dedupe_key: Mapped[str] = mapped_column(
        sa.String(160), default='', unique=True, comment='幂等键 workflow_uuid:fire_at'
    )
    status: Mapped[str] = mapped_column(sa.String(20), default='running', comment=WORKFLOW_RUN_STATUS_COMMENT)
    advance_mode: Mapped[str] = mapped_column(
        sa.String(10),
        default='manual',
        comment='推进档位 (manual:逐环派发:blue/auto:自动接力:green)，默认 manual；可运行中翻转（W-S1 §5.1）',
    )
    driver_node_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='W5 驱动权租约：唯一推进者节点 ID'
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='W5 driver 租约到期（超时可被 CAS 接管）'
    )
    graph_snapshot: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='fire 时固化的 nodes+edges 快照'
    )
    output_summary: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='整图终态综合（无出边末端节点拼接，可标 is_sink 覆盖）'
    )
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='完成时间')
