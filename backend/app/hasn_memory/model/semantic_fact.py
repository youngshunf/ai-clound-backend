"""语义事实（doc 04 §6，原 public.semantic_facts，去复数 semantic_fact）。

四主体四层记忆线之 semantic 语义层云端镜像。**镜像本地 crate 结构**：ULID 主键、epoch ms
时间戳、JSON 串字段（object_json/source_*_json）、置信度，与本地字段名严格双端一致（doc 04 §14）。
继承 MappedBase 手写 schema；本模型仅元数据登记（ADR-15 收编 P3）。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class SemanticFact(MappedBase):
    """HASN 记忆系统 - 语义事实。"""

    __tablename__ = 'semantic_fact'
    __table_args__ = (
        sa.CheckConstraint(
            "subject_kind IN ('owner', 'agent_self', 'peer', 'world')", name='ck_semantic_fact_subject_kind'
        ),
        sa.CheckConstraint("memory_layer = 'semantic'", name='ck_semantic_fact_layer'),
        sa.CheckConstraint(
            "scope_kind IN ('global', 'workspace', 'project', 'task', 'conversation', 'topic')",
            name='ck_semantic_fact_scope_kind',
        ),
        sa.CheckConstraint(
            "(subject_kind = 'agent_self' AND agent_id IS NOT NULL)"
            " OR (subject_kind IN ('owner', 'peer', 'world') AND agent_id IS NULL)",
            name='ck_semantic_fact_agent_id',
        ),
        sa.CheckConstraint("subject_kind != 'world' OR scope_kind != 'global'", name='ck_semantic_fact_world_scope'),
        {'comment': 'HASN 记忆系统 - 语义事实', 'schema': _SCHEMA},
    )

    fact_id = sa.Column(sa.String(40), primary_key=True, comment='Fact ID')
    owner_id = sa.Column(sa.String(40), nullable=False, comment='Owner ID')
    agent_id = sa.Column(sa.String(40), comment='Agent ID (仅 agent_self 时填)')
    subject_kind = sa.Column(sa.String(16), nullable=False, comment='主体类型 (owner/agent_self/peer/world)')
    subject_id = sa.Column(sa.String(40), nullable=False, comment='主体 ID')
    memory_layer = sa.Column(sa.String(16), nullable=False, server_default='semantic', comment='记忆层次 (semantic)')
    scope_kind = sa.Column(sa.String(16), nullable=False, server_default='global', comment='作用域类型')
    scope_id = sa.Column(sa.String(40), nullable=False, comment='作用域 ID')
    predicate = sa.Column(sa.Text, nullable=False, comment='谓词')
    object_json = sa.Column(sa.Text, nullable=False, comment='对象 JSON')
    confidence = sa.Column(sa.Float, nullable=False, comment='置信度')
    status = sa.Column(sa.String(16), nullable=False, server_default='active', comment='状态 (active/superseded/disputed/withdrawn)')
    superseded_by = sa.Column(sa.String(40), comment='被替代的 fact_id')
    source_turn_ids = sa.Column(sa.Text, nullable=False, server_default='[]', comment='来源 turn ID 列表')
    source_refs_json = sa.Column(sa.Text, nullable=False, server_default='[]', comment='来源引用 JSON')
    rationale = sa.Column(sa.Text, comment='理由')
    created_at = sa.Column(sa.BigInteger, nullable=False, comment='创建时间 (epoch ms)')
    updated_at = sa.Column(sa.BigInteger, nullable=False, comment='更新时间 (epoch ms)')
