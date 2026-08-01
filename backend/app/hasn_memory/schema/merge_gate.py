"""云端合并闸 DTO（doc19 §5.5 / §5.6）。

设计事实源：``docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md``

主脑分身在**自己的设备上**跑完一轮合并（规则去重 + hint 快速通道 + 必要时 LLM 裁决 + 画像
重算），把**整轮结果**提交云端合并闸 apply。云端只做「校验 + 落库 + 推进游标」，不做任何语义
判断（§8.5）——所有字段都是主脑已经算好的结论，云端不重算、不补算、不猜。

契约必须与 hasn-node 侧 `hasn.memory.merge` 的提交体逐字段对齐：字段名一旦漂移，主脑提交会
被 422 拒在门外，而合并停摆在 §5.5 的可见性面上只会显示成「主脑很久没整理了」——排查成本极高。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.common.schema import SchemaBase

#: 主体类别（与 `semantic_fact` 表 CHECK `ck_semantic_fact_subject_kind` 一致）。
SubjectKind = Literal['owner', 'agent_self', 'peer', 'world']
#: 作用域类别（与表 CHECK `ck_semantic_fact_scope_kind` 一致）。
ScopeKind = Literal['global', 'workspace', 'project', 'task', 'conversation', 'topic']


class MergeVerdictItem(SchemaBase):
    """一条合并裁决（overlay 三列，§3.4）。"""

    fact_id: str = Field(max_length=40, description='被裁决的事实 ID')
    verdict: Literal['merged_into', 'disputed'] = Field(
        description='裁决 (merged_into:已并入派生事实/disputed:矛盾待主人确认)'
    )
    judged_revision: int = Field(ge=0, description='裁决所依据的 revision；与库中当前值不等即该条作废（§3.4 失效护栏）')


class MergeDerivedFactItem(SchemaBase):
    """一条合并派生事实（`origin_kind='merged'`，§3.2）。"""

    fact_id: str = Field(max_length=40, description='派生事实 ID（主脑本地铸造，作幂等键）')
    predicate: str = Field(min_length=1, description='谓词')
    object_json: Any = Field(None, description='对象（JSON 串或原生值；落库统一存合法 JSON 串）')
    subject_kind: SubjectKind = Field(description='主体类别')
    subject_id: str = Field(max_length=40, description='主体 ID')
    scope_kind: ScopeKind = Field('global', description='作用域类别')
    scope_id: str | None = Field(None, description='作用域 ID（缺省回落主体 ID）')
    confidence: float = Field(0.9, ge=0.0, le=1.0, description='置信度')
    merged_from: list[str] = Field(default_factory=list, description='被合并的源 fact_id 数组（血缘）')
    rationale: str | None = Field(None, description='合并理由（面向主人可解释）')


class MergeOwnerMemoryPayload(SchemaBase):
    """主脑重算后的 USER.md 正文（无变化时整个字段不传）。"""

    content: str | None = Field(None, description='重算后的 USER.md 正文')


class MergePeerPortraitItem(SchemaBase):
    """主脑重算后的一份 peer 画像。"""

    peer_hasn_id: str = Field(max_length=40, description='对方 HASN ID')
    portrait_text: str = Field(min_length=1, description='画像正文')
    peer_kind: Literal['human', 'agent'] | None = Field(None, description='对方类别（缺省按 hasn_id 前缀判定）')


class MergeStats(SchemaBase):
    """主脑自报的本轮裁决计数（云端只登记，不重算——§8.5 云端不做语义处理）。"""

    facts_judged: int = Field(0, ge=0, description='本轮读入裁决的活跃事实数')
    facts_merged: int = Field(0, ge=0, description='本轮标 merged_into 的事实数')
    facts_disputed: int = Field(0, ge=0, description='本轮标 disputed 的事实数')


class MergeApplyRequest(SchemaBase):
    """整轮合并结果提交体（§5.6：整轮原子，任一校验不符整轮拒绝）。"""

    run_id: str = Field(min_length=1, max_length=40, description='合并轮次 ID（主脑本地生成，幂等键）')
    node_id: str = Field(min_length=1, max_length=64, description='提交节点 node_id（必须是主脑当前绑定节点）')
    base_owner_memory_version: int = Field(
        ge=0, description='提交声明的基线 owner_memory.version（CAS 依据；无行时为 0）'
    )
    verdicts: list[MergeVerdictItem] = Field(default_factory=list, description='overlay 裁决（整轮替换上一轮）')
    derived_facts: list[MergeDerivedFactItem] = Field(default_factory=list, description='派生事实集')
    owner_memory: MergeOwnerMemoryPayload | None = Field(None, description='重算后的 USER.md（无变化时不传）')
    peer_portraits: list[MergePeerPortraitItem] = Field(default_factory=list, description='重算后的 peer 画像集')
    summary: str | None = Field(None, description='给主人看的人话摘要')
    stats: MergeStats = Field(default_factory=MergeStats, description='本轮裁决计数')


class SkippedVerdict(SchemaBase):
    """被逐条失效护栏跳过的裁决（§3.4）——**不是整轮失败**，留待下轮重裁。"""

    fact_id: str = Field(description='被跳过的事实 ID')
    reason: Literal['verdict_stale', 'fact_not_found'] = Field(
        description='跳过原因 (verdict_stale:事实已被本地整理或主人改过/fact_not_found:该事实尚未汇聚或已被硬删)'
    )
    judged_revision: int = Field(description='裁决所依据的 revision')
    current_revision: int | None = Field(None, description='库中当前 revision（fact_not_found 时为 None）')


class MergeApplyResponse(SchemaBase):
    """合并闸 apply 结果。"""

    applied: bool = Field(description='整轮是否已应用')
    run_id: str = Field(description='本轮 run_id')
    new_owner_memory_version: int = Field(description='应用后的 owner_memory.version')
    skipped_verdicts: list[SkippedVerdict] = Field(default_factory=list, description='被失效护栏跳过的裁决')
    derived_created: int = Field(0, description='本次新建的派生事实数（重放时为 0）')
    portraits_updated: int = Field(0, description='本次写入的画像数（重放时为 0）')
    replayed: bool = Field(False, description='是否为同一 run_id 的幂等重放（True 时未再次落库）')


class MergeRequestBody(SchemaBase):
    """非主脑分身发起合并请求（§5.5：主脑离线时落云端每 owner 待办）。"""

    node_id: str = Field(min_length=1, max_length=64, description='发起节点 node_id')
    reason: str | None = Field(None, max_length=64, description='请求原因（local_review_done / owner_manual 等）')


class PendingMergeRequest(SchemaBase):
    """尚未被主脑消化的合并待办。"""

    requested_time: datetime = Field(description='最近一次请求时间')
    requested_by_agent: str = Field(description='发起请求的分身 hasn_id')
    requested_by_node: str = Field(description='发起请求的节点 node_id')
    reason: str | None = Field(None, description='请求原因')
    pending_days: float = Field(description='待办滞留天数')


class MergeRequestResponse(SchemaBase):
    """合并待办登记结果（每主人至多一条，重复请求覆盖）。"""

    accepted: bool = Field(description='是否已登记')
    is_master_brain: bool = Field(description='发起者本身是否就是当前主脑（是则应直接本地合并，别绕待办）')
    pending: PendingMergeRequest = Field(description='登记后的待办快照')


class MergeStatusResponse(SchemaBase):
    """§5.5 主脑单点可见性：主人记忆页「上次整理于 X，主脑在 <设备> 上，当前离线」的数据源。"""

    owner_memory_version: int = Field(description='当前 owner_memory 版本（0 表示尚未合并过）')
    owner_memory_edited: bool = Field(
        False,
        description=(
            '主人是否手工改过档案正文且尚未被重算消费（doc19 §4.6）。'
            'true 时记忆页应显示「你手工改过档案正文，下次整理会尽量保留你的表述」'
        ),
    )
    last_merge_run_id: str | None = Field(None, description='上次成功合并的 run_id')
    last_merge_time: datetime | None = Field(None, description='上次成功合并的时间')
    last_merge_node_id: str | None = Field(None, description='上次成功合并的执行节点 node_id')
    last_merge_node_name: str | None = Field(None, description='上次成功合并的执行节点名称（节点已注销时为 None）')
    last_merge_agent_id: str | None = Field(None, description='上次成功合并的主脑分身 hasn_id')
    last_merge_summary: str | None = Field(None, description='上次成功合并的人话摘要')
    days_since_last_merge: float | None = Field(None, description='距上次成功合并的天数（从未合并为 None）')
    master_brain_agent_id: str | None = Field(None, description='当前主脑分身 hasn_id（无活跃分身为 None）')
    master_brain_node_id: str | None = Field(None, description='当前主脑所在节点 node_id（尚未上报绑定为 None）')
    master_brain_node_name: str | None = Field(None, description='当前主脑所在节点名称')
    master_brain_online: bool | None = Field(
        None, description='主脑所在节点是否在线；**None = 判不了**（节点未知或在线状态源不可用），不猜'
    )
    has_pending_request: bool = Field(False, description='当前是否有未消化的合并待办')
    pending_request: PendingMergeRequest | None = Field(None, description='未消化的合并待办详情')
    last_rejected_run_id: str | None = Field(None, description='最近一次被合并闸拒绝的 run_id（无则 None）')
    last_rejected_reason: str | None = Field(
        None, description='最近一次拒绝原因（not_master_brain / version_conflict）'
    )
    last_rejected_time: datetime | None = Field(None, description='最近一次拒绝的时间')
    stale_over_threshold: bool = Field(
        False, description='是否超过阈值未成功合并（如实告知，不是错误——§5.5「不接受静默」）'
    )
    stale_threshold_days: int = Field(description='阈值天数（建议 7 天）')
