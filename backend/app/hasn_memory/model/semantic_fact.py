"""语义事实（doc 04 §6，原 public.semantic_facts，去复数 semantic_fact）。

四主体四层记忆线之 semantic 语义层云端镜像。**镜像本地 crate 结构**：ULID 主键、epoch ms
时间戳、JSON 串字段（object_json/source_*_json）、置信度，与本地字段名严格双端一致（doc 04 §14）。
继承 MappedBase 手写 schema；本模型仅元数据登记（ADR-15 收编 P3）。

doc19 S3 增列（双端一致，见 19-多节点记忆分层与分身自治整理设计.md §3.2/§3.4/§8.2）：

- **溯源三列** `origin_kind` / `origin_node_id` / `origin_agent_id`：每条事实记录它产自哪个
  节点、由哪个分身写入——「谁有权整理」「这条为什么被改了」的地基。该列组**不建外键**，
  `merged` / `retired` 等非实体取值不该撑在外键语义列上；
- **血缘** `merged_from`：合并派生事实指向被合并的 fact_id 数组（JSON 串）；
- **业务字段组版本** `revision`：正文/rationale/confidence/status/valid_until 每次变更 +1，
  兼作上行幂等键成分与 overlay 失效判据；
- **合并裁决 overlay** `merge_verdict` / `merge_verdict_run` / `merge_judged_revision`：
  只有合并写，与业务字段组写者集合不相交，天然无写写冲突。生效可见性判据见
  `semantic_fact_service.effective_fact_condition`——裁决所依据的 revision 与当前不等即作废；
- **时效** `valid_until`：可空 epoch ms，review 复盘候选③依据。

doc19 S2 增列：

- **本人纠正指向** `supersedes_hint`（§4.3 / D-21）：`save` 携带的「我从前记的那条错了」线索，
  与 `superseded_by` 严格区分——后者是**已生效**的取代关系，前者只是待裁决的线索，是否生效由
  S6 合并规则层判（hint 指向的旧事实 `origin_agent_id` 与新事实一致 = 本人纠正本人 → 直接按
  hint 裁决，不调 LLM；指向他人事实只作 LLM 参考信号）。不建外键：hint 可指向另一节点上、
  本库尚未汇聚的 fact_id，也可指向已被主人 purge 的事实。

⚠️ `origin_agent_id` 记录**全部主体类别**的写入分身；既有 `agent_id` 列受
`ck_semantic_fact_agent_id` 约束仅 `agent_self` 可填，两者语义不同，勿混用。
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
        # doc19 §3.2：node=节点自产 / merged=合并派生 / retired=产生节点已退役
        sa.CheckConstraint("origin_kind IN ('node', 'merged', 'retired')", name='ck_semantic_fact_origin_kind'),
        # doc19 §3.4：overlay 只由合并写；NULL=未裁决
        sa.CheckConstraint(
            "merge_verdict IS NULL OR merge_verdict IN ('merged_into', 'disputed')",
            name='ck_semantic_fact_merge_verdict',
        ),
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

    # ---- doc19 §3.2 溯源与血缘（列组不建外键）----
    origin_kind = sa.Column(
        sa.String(16), nullable=False, server_default='node', comment='溯源类别 (node/merged/retired)'
    )
    origin_node_id = sa.Column(sa.String(64), comment='产生节点 node_id（merged 时为空；未知节点留空）')
    origin_agent_id = sa.Column(sa.String(40), comment='写入分身 hasn_id（全部主体类别都记；merged 时为空）')
    merged_from = sa.Column(
        sa.Text, nullable=False, server_default='[]', comment='被合并的 fact_id 数组 JSON（派生事实血缘）'
    )
    # ---- doc19 §3.4 业务字段组版本 ----
    revision = sa.Column(sa.BigInteger, nullable=False, server_default='1', comment='业务字段组版本（每次变更 +1）')
    # ---- doc19 §3.4 合并裁决 overlay（唯一写者是合并）----
    merge_verdict = sa.Column(sa.String(16), comment='合并裁决 (NULL/merged_into/disputed)')
    merge_verdict_run = sa.Column(sa.String(40), comment='作出该裁决的合并轮次 run_id')
    merge_judged_revision = sa.Column(sa.BigInteger, comment='裁决所依据的 revision（与当前不等即裁决过期作废）')
    # ---- doc19 §8.2 时效 ----
    valid_until = sa.Column(sa.BigInteger, comment='有效期截止 (epoch ms)；时效性事实，review 候选③依据')
    # ---- doc19 §4.3 本人纠正快速通道指向（D-21）----
    supersedes_hint = sa.Column(
        sa.String(40), comment='save 携带的本人纠正指向旧 fact_id；只是线索非已生效取代，由合并规则层裁决'
    )
