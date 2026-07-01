"""Peer 画像云端权威表（doc17 PEERSYN-P1 · G1）。

按 (owner_id, peer_hasn_id) 唯一——owner 视角唯一，跨该 owner 名下**全部分身**对同一对方的
观察合并成一份画像（多 Agent 合并在语义事实层已 owner-scoped，本表只承载合成后的叙述）。

**镜像本地 crate `hasn-memory` 的 peer_portraits 表**（epoch ms 时间戳、复合主键、peer_kind
CHECK），但**不含** revision/synced_at/is_dirty（本地 SQLite 同步簿记，云端权威无需）。继承
MappedBase 手写 schema（同 semantic_fact——非标准 fba 结构，纯镜像登记）。

下行：合成后经 hasn_sync_service 发 `memory.peer_portrait.upserted`（namespace='portraits'）
→ daemon MemorySyncPullApplier::apply_peer_portrait 落本地镜像 → PromptData::load 注入 runtime。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class PeerPortrait(MappedBase):
    """HASN 记忆系统 - Peer 画像。"""

    __tablename__ = 'peer_portrait'
    __table_args__ = (
        sa.CheckConstraint("peer_kind IN ('human', 'agent')", name='ck_peer_portrait_peer_kind'),
        {'comment': 'HASN 记忆系统 - Peer 画像（owner 视角唯一，跨分身合并）', 'schema': _SCHEMA},
    )

    owner_id = sa.Column(sa.String(40), primary_key=True, comment='Owner ID')
    peer_hasn_id = sa.Column(sa.String(40), primary_key=True, comment='对方 HASN ID（h_/a_ 均可）')
    peer_kind = sa.Column(sa.String(16), nullable=False, server_default='human', comment='对方类型 (human/agent)')
    portrait_text = sa.Column(sa.Text, nullable=False, server_default='', comment='画像叙述正文')
    language = sa.Column(sa.String(8), nullable=False, server_default='zh', comment='语言')
    version = sa.Column(sa.BigInteger, nullable=False, server_default='1', comment='画像版本（单调递增）')
    revised_by = sa.Column(sa.String(40), nullable=False, server_default='system', comment='修订者')
    source_fact_count = sa.Column(sa.Integer, nullable=False, server_default='0', comment='合成纳入的 active 事实数')
    last_synthesized_at = sa.Column(sa.BigInteger, comment='最后合成时间 (epoch ms)')
    last_interaction_at = sa.Column(sa.BigInteger, comment='最后交互时间 (epoch ms)')
    token_count = sa.Column(sa.Integer, nullable=False, server_default='0', comment='粗估 token 数')
    created_at = sa.Column(sa.BigInteger, nullable=False, comment='创建时间 (epoch ms)')
    updated_at = sa.Column(sa.BigInteger, nullable=False, comment='更新时间 (epoch ms)')
