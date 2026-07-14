"""工作流节点执行态模型（hasn_task.workflow_node_run）。

节点执行专属表（工作流应用产品化 P1 expand-only 迁移）：与借道的 hasn_task.run
节点执行行并存双写，读侧优先本表。承载节点状态机、最新工作会话、产出物、
产出闸/质量门重试计数、需处理原因。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/11-场景与工作流产品化.md。
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key

# 节点执行态字典（value:label:color），与迁移 SQL COMMENT 保持一致
WORKFLOW_NODE_RUN_STATUS_COMMENT = (
    '状态 (pending:未开始:gray/ready:可派发:blue/running:分身工作中:orange/waiting:待你决策:orange/'
    'needs_attention:需要处理:red/done:已完成:green/failed:执行失败:red/skipped:已提供:gray/'
    'stale:基于旧产物:orange/cancelled:已取消:gray)'
)


class HasnWorkflowNodeRun(HasnTaskAppBase):
    """工作流节点执行态表"""

    __tablename__ = 'workflow_node_run'

    id: Mapped[id_key] = mapped_column(init=False)
    node_run_uuid: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='端云稳定节点执行 UUID（前缀 ndr_，同步主键）'
    )
    workflow_run_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属工作流执行实例稳定 UUID')
    workflow_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属工作流稳定 UUID（冗余便于查询）')
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属 owner')
    node_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='图内节点标识')
    status: Mapped[str] = mapped_column(sa.String(20), default='pending', comment=WORKFLOW_NODE_RUN_STATUS_COMMENT)
    work_session_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='最新工作会话（历史经 origin_ref 反查）'
    )
    artifacts: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='产出物 [{artifact_id,is_current}]'
    )
    output_summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='产出摘要')
    output_gate_retries: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='产出闸重试次数（P2）')
    review_rejects: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='质量门驳回次数（P4）')
    attention_reason: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='需要处理的原因')
    started_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始时间')
    completed_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='完成时间')
