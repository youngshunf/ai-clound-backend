"""工作流依赖边模型（hasn_task.workflow_edge）。

DAG 边：parent_node_key → child_node_key。建/改时 DFS 环检测；云端 push 落库前复验无环
（07 §5.3，双设备并发加边合并成环的最后防线）。边引用 workflow_uuid + node_key（跨 schema
无 FK），删节点由 service 层强制先删其全部入/出边，禁止悬空边。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §4.1。
"""

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import id_key


class HasnWorkflowEdge(HasnTaskAppBase):
    """工作流依赖边表"""

    __tablename__ = 'workflow_edge'
    __table_args__ = (
        sa.UniqueConstraint('workflow_uuid', 'parent_node_key', 'child_node_key', name='uq_workflow_edge'),
        {'comment': '工作流依赖边', 'schema': 'hasn_task'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    workflow_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属工作流稳定 UUID')
    parent_node_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='父节点 node_key')
    child_node_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='子节点 node_key')
