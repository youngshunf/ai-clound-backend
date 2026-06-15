"""原始对话 turn + embedding（doc 04 §5，原 public.episodic_turns，去复数 episodic_turn）。

四主体四层记忆线之 episodic 原始层的云端镜像。**镜像本地 crate（hasn-memory SQLite）结构**：
ULID 主键、epoch ms 时间戳（created_at/deleted_at bigint）、embedding bytea，与本地字段名严格
双端一致（doc 04 §14 禁漂移），故继承 MappedBase 手写 schema，而非标准 fba Base。

本表为云端同步镜像，读写由记忆同步链路负责；本模型仅元数据登记（ADR-15 收编 P3：消除孤儿表）。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class EpisodicTurn(MappedBase):
    """HASN 记忆系统 - 原始对话 turn + embedding。"""

    __tablename__ = 'episodic_turn'
    __table_args__ = (
        sa.CheckConstraint("role IN ('owner', 'agent', 'peer', 'system')", name='ck_episodic_turn_role'),
        {'comment': 'HASN 记忆系统 - 原始对话 turn + embedding', 'schema': _SCHEMA},
    )

    turn_id = sa.Column(sa.String(40), primary_key=True, comment='Turn ID')
    conversation_id = sa.Column(sa.String(40), nullable=False, comment='会话 ID')
    owner_id = sa.Column(sa.String(40), nullable=False, comment='Owner ID')
    agent_id = sa.Column(sa.String(40), nullable=False, comment='Agent ID')
    seq = sa.Column(sa.Integer, nullable=False, comment='会话内单调递增序号')
    role = sa.Column(sa.String(16), nullable=False, comment='角色 (owner/agent/peer/system)')
    sender_hasn_id = sa.Column(sa.String(40), nullable=False, comment='发送者 HASN ID')
    content_text = sa.Column(sa.Text, nullable=False, comment='内容文本')
    content_tokens = sa.Column(sa.Integer, nullable=False, comment='内容 token 数')
    embedding = sa.Column(sa.LargeBinary, comment='Embedding 向量')
    topic_chunk_id = sa.Column(sa.String(40), comment='话题分片 ID')
    created_at = sa.Column(sa.BigInteger, nullable=False, comment='创建时间 (epoch ms)')
    deleted_at = sa.Column(sa.BigInteger, comment='删除时间 (epoch ms)')
