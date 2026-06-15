"""时序事件（doc 04 §7，原 public.memory_events，去复数 memory_event）。

四主体四层记忆线之 episodic 事件层云端镜像。**镜像本地 crate 结构**：ULID 主键、epoch ms
时间戳、embedding bytea、JSON 串字段，与本地字段名严格双端一致（doc 04 §14）。
继承 MappedBase 手写 schema；本模型仅元数据登记（ADR-15 收编 P3）。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class MemoryEvent(MappedBase):
    """HASN 记忆系统 - 时序事件。"""

    __tablename__ = 'memory_event'
    __table_args__ = (
        sa.CheckConstraint(
            "subject_kind IN ('owner', 'agent_self', 'peer', 'world')", name='ck_memory_event_subject_kind'
        ),
        sa.CheckConstraint("memory_layer = 'episodic'", name='ck_memory_event_layer'),
        sa.CheckConstraint(
            "scope_kind IN ('global', 'workspace', 'project', 'task', 'conversation', 'topic')",
            name='ck_memory_event_scope_kind',
        ),
        sa.CheckConstraint(
            "(subject_kind = 'agent_self' AND agent_id IS NOT NULL)"
            " OR (subject_kind IN ('owner', 'peer', 'world') AND agent_id IS NULL)",
            name='ck_memory_event_agent_id',
        ),
        sa.CheckConstraint("subject_kind != 'world' OR scope_kind != 'global'", name='ck_memory_event_world_scope'),
        {'comment': 'HASN 记忆系统 - 时序事件', 'schema': _SCHEMA},
    )

    event_id = sa.Column(sa.String(40), primary_key=True, comment='Event ID')
    owner_id = sa.Column(sa.String(40), nullable=False, comment='Owner ID')
    agent_id = sa.Column(sa.String(40), comment='Agent ID (仅 agent_self 时填)')
    subject_kind = sa.Column(sa.String(16), nullable=False, comment='主体类型')
    subject_id = sa.Column(sa.String(40), nullable=False, comment='主体 ID')
    memory_layer = sa.Column(sa.String(16), nullable=False, server_default='episodic', comment='记忆层次 (episodic)')
    scope_kind = sa.Column(sa.String(16), nullable=False, server_default='conversation', comment='作用域类型')
    scope_id = sa.Column(sa.String(40), nullable=False, comment='作用域 ID')
    event_type = sa.Column(sa.String(40), nullable=False, comment='事件类型')
    summary = sa.Column(sa.Text, nullable=False, comment='摘要')
    detail = sa.Column(sa.Text, comment='详情')
    related_peer_ids = sa.Column(sa.Text, nullable=False, server_default='[]', comment='相关 peer ID 列表')
    related_agent_ids = sa.Column(sa.Text, nullable=False, server_default='[]', comment='相关 agent ID 列表')
    source_conversation_id = sa.Column(sa.String(40), comment='来源会话 ID')
    source_turn_ids = sa.Column(sa.Text, nullable=False, server_default='[]', comment='来源 turn ID 列表')
    source_refs_json = sa.Column(sa.Text, nullable=False, server_default='[]', comment='来源引用 JSON')
    occurred_at = sa.Column(sa.BigInteger, nullable=False, comment='发生时间 (epoch ms)')
    created_at = sa.Column(sa.BigInteger, nullable=False, comment='创建时间 (epoch ms)')
    embedding = sa.Column(sa.LargeBinary, comment='Embedding 向量')
    deleted_at = sa.Column(sa.BigInteger, comment='删除时间 (epoch ms)')
