"""记忆命名空间权威 revision 模型（原 public.memory_namespace_revisions，去前缀 namespace_revision）。

同步分区（owner/agent）× 命名空间维度的单调递增 revision，由 `hasn_sync_service` 推进。
**实际读写由 `hasn_sync_service` 裸 SQL 完成**（全限定 `hasn_memory.namespace_revision`）；
本模型仅用于 ORM 元数据登记（ADR-15 收编 P2：消除"无模型 → 自动对账/alembic 误判孤儿"风险），
**不改变既有裸 SQL 查询路径**。复合主键 + 仅 updated_time（无 created 由 DateTimeMixin），
故继承 MappedBase 并手写 __table_args__ 注入 schema，而非标准 fba Base。

表结构以 `backend/sql/hasn/memory_namespace_revisions.sql` 为准。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class MemoryNamespaceRevision(MappedBase):
    """HASN 记忆命名空间权威 revision（同步中枢）。"""

    __tablename__ = 'namespace_revision'
    __table_args__ = (
        sa.CheckConstraint("sync_scope_kind IN ('owner', 'agent')", name='ck_namespace_revision_scope_kind'),
        {'comment': 'HASN 记忆命名空间权威 revision 表', 'schema': _SCHEMA},
    )

    sync_scope_kind = sa.Column(sa.String(16), primary_key=True, comment='同步分区类型 (owner/agent)')
    sync_scope_id = sa.Column(sa.String(40), primary_key=True, comment='同步分区 ID（owner_id 或 agent_id）')
    namespace = sa.Column(sa.String(40), primary_key=True, comment='记忆命名空间（portraits/facts/tasks 等）')
    revision = sa.Column(sa.BigInteger, nullable=False, server_default='0', comment='命名空间维度单调递增 revision')
    last_event_id = sa.Column(sa.String(40), comment='最近一次触发该命名空间 revision 的服务端事件 ID')
    updated_at = sa.Column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='命名空间权威 revision 更新时间'
    )
    created_time = sa.Column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'
    )
    updated_time = sa.Column(sa.DateTime(timezone=True), comment='更新时间')
