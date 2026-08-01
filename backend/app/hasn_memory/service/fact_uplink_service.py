"""语义事实上行业务事件的云端 apply（doc19 S5-cloud）。

设计事实源：``docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md``
  §3.3 自产片 vs 回灌片 · §3.4 两层状态（业务字段组 / 合并裁决 overlay） · §4.5 purge 级联与墓碑
  · §4.6 主人第三类写者 · §8.3 六条硬约束 · §8.5 云端职责边界
事故教训：``实施/98-记忆上推链路整体退役与本地镜像单向化实施方案.md``——「两侧白名单不对称
是事故直接成因」「毒丸事件无隔离：永久拒绝无丢弃/退避/去重 → 5s 无限重推、日志洪水 94%」。

**本模块只做「校验 + 落库 + 推进游标」，不做任何记忆语义处理**（§8.5：云端不合并、不检索、
不提取、不合成画像）。判断力在 hasn-node 上的分身身上。

--------------------------------------------------------------------------------------
六个上行事件的完整契约（两侧对称，本地构造点见 ``hasn-node/crates/hasn-memory/src/storage/
authority.rs::MemoryOp::fact_event_type``）
--------------------------------------------------------------------------------------

===================================  ==========================================================
事件                                  载荷 / 落列 / 拒绝语义
===================================  ==========================================================
``memory.fact.saved``                 完整事实快照 → 插入业务字段组 + 溯源四列 + ``revision``；
                                      ``origin_node_id`` 必须等于推送节点，否则 **永久拒绝**
``memory.fact.updated``               完整事实快照 → 覆盖业务字段组 + ``revision``；行必须已存在
``memory.fact.superseded``            同上，另落 ``superseded_by`` 与 ``status='superseded'``
``memory.fact.withdrawn``             同上，``status='withdrawn'``
``memory.fact.merge_verdict``         **只写 overlay 三列**，绝不碰业务字段组、绝不推进 revision
``memory.fact.purged``                物理删除 + 沿 ``merged_from`` 递归级联 + 墓碑 + 画像重算待办
===================================  ==========================================================

三个整理事件（``updated`` / ``superseded`` / ``withdrawn``）的 origin 守卫**按写者分档**：载荷
带 [`OWNER_WRITE_MARKER`] 的是**主人整理**（§4.6 / D-20 第三类写者，凌驾节点边界），跳过 origin
守卫、只判归属主人；不带标记的是**分身整理**，只能改本节点自产片。详见
``FactUplinkService._assert_editable_own_slice``（含该标记的越权风险评估）。

拒绝三分类（§8.3-4，照实施/98 契约；``禁止让本地无退避重推``）：

- **永久拒绝**——墓碑命中（8045，回令 ``purge_local``）、origin 不符（8044）、schema 非法（8043）。
  daemon 丢弃 outbox 行 + 一次性 ``warn``，不再重推；
- **冲突**——事实行尚未汇聚（``save`` 还在路上）复用既有 8041，daemon 退避重试；
- **瞬时失败**——DB 抖动等由异常上抛为 5xx / worker 重试。

**日志等级铁律**：重放、跳号、永久拒绝一律 ``warn``（可自愈 / 4xx 语义）；只有不变量破坏才
``error``。实施/98 的洪水正是「可恢复错误被当成终局故障反复刷屏」，这里绝不重演。
"""

from __future__ import annotations

import json

from collections import OrderedDict
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa

from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn_memory.service.fact_tombstone_service import fact_tombstone_service
from backend.app.hasn_memory.service.merge_request_service import merge_request_service
from backend.common.log import log

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


# ======================================================================================
# 一、事件白名单（§8.3-5：两侧白名单必须逐条对称）
# ======================================================================================

#: 语义事实上行的六个业务事件（§8.3-1：上行的是**业务事件**，不是脏行快照）。
#:
#: ⚠️ **两侧白名单不对称正是实施/98 事故的直接成因**——本地构造点在
#: ``hasn-node/crates/hasn-memory/src/storage/authority.rs`` 的
#: ``MemoryOp::fact_event_type()``，那边每改一个字符串，这里必须同步；反之亦然。
#: 对称性由 ``backend/tests/hasn_memory/test_doc19_fact_uplink_pg.py`` 的钉子测试守住
#: （本地仓在场时逐条比对真实 Rust 源文件，不在场时退化为显式期望集合钉死）。
MEMORY_FACT_UPLINK_EVENTS = frozenset(
    {
        'memory.fact.saved',
        'memory.fact.updated',
        'memory.fact.superseded',
        'memory.fact.withdrawn',
        'memory.fact.purged',
        'memory.fact.merge_verdict',
    }
)

#: 只改业务字段组的三个整理事件（``saved`` 另走插入分支）。
_BUSINESS_UPDATE_EVENTS = frozenset(
    {'memory.fact.updated', 'memory.fact.superseded', 'memory.fact.withdrawn'}
)

#: 主人事件标记键（doc19 §4.6 / D-20）。
#:
#: 主人是**凌驾节点边界的第三类写者**：他从设备 X 的记忆页确认一条 origin=设备 Y 的事实必须
#: 成立。事件名沿用上面的六件套（§8.3-5 明令两侧白名单不许单边新增），载荷信封多带这一个布尔
#: 标记区分「主人写」与「分身整理」。
#:
#: 本地对照点（逐字一致，改一侧必须同步另一侧）：
#: ``hasn-node/crates/hasn-memory/src/storage/facts.rs::OWNER_WRITE_MARKER``，写入点是同文件的
#: ``owner_fact_snapshot_json``——标记只加在**信封层**，与 ``object_json`` 等内容子树无关，故
#: push 端点的私有元数据扫描（``_contains_private_runtime_key_for_event``）不会误伤。
OWNER_WRITE_MARKER = 'owner_write'

#: 受墓碑防复活约束的事件（§8.3-6）。``purged`` 不在其中——墓碑已在即「已应用」，不是拒绝。
_TOMBSTONE_GUARDED_EVENTS = MEMORY_FACT_UPLINK_EVENTS - {'memory.fact.purged'}


# ======================================================================================
# 二、错误码登记表（8xxx 段）
# ======================================================================================
#
# 既有占位（勿复用）：
#   8034 ERR_RUNTIME_PRIVATE_METADATA_REJECTED   sync.py / hasn_task sync.py
#   8035 ERR_MEMORY_SYNC_SCOPE_INVALID           hasn_sync_service.py
#   8036 ERR_MEMORY_NAMESPACE_UNKNOWN            _sync_codec.py
#   8037 ERR_TASK_SYNC_EVENT_UNSUPPORTED         hasn_sync_service.py / hasn_task sync.py
#   8038 ERR_TASK_SYNC_CONFLICT                  hasn_sync_service.py
#   8040 ERR_SYNC_EVENT_UNSUPPORTED              sync.py
#   8041 ERR_SYNC_EVENT_CONFLICT                 sync.py（本模块复用为「可退避重试」类）
#   8042 ERR_TASK_SYNC_INBOX_CONFLICT            hasn_task sync.py
# 本模块新增：8043 / 8044 / 8045。
#
# ⚠️ **客户端侧必须同步登记**：hasn-node 的
# ``crates/hasn-node/src/backend/types/sync.rs::SyncRejected::is_permanent()`` 现为
# ``matches!(self.code, 8034 | 8037 | 8040)``——新三码未加入前，daemon 会把它们当未知码走
# **退避重试**（不会洪水，但永久拒绝的事件不会出队、``purge_local`` 指令也不会被执行）。

_PAYLOAD_INVALID_ERROR = ErrorObject(
    code=8043,
    name='ERR_MEMORY_FACT_PAYLOAD_INVALID',
    message='记忆事实上行载荷不合法',
)
_ORIGIN_MISMATCH_ERROR = ErrorObject(
    code=8044,
    name='ERR_MEMORY_FACT_ORIGIN_MISMATCH',
    message='节点只能上行自己的自产片事实',
)
_PURGED_ERROR = ErrorObject(
    code=8045,
    name='ERR_MEMORY_FACT_PURGED',
    message='该事实已被主人彻底删除，晚到事件永久拒绝',
)
_CONFLICT_ERROR = ErrorObject(
    code=8041,
    name='ERR_SYNC_EVENT_CONFLICT',
    message='事实行尚未汇聚到云端，稍后重试',
)

#: 供契约测试钉死的错误码分配表（新增码必须同时出现在这里和上面的注释里）。
MEMORY_FACT_UPLINK_ERROR_CODES: Mapping[str, int] = {
    'ERR_MEMORY_FACT_PAYLOAD_INVALID': 8043,
    'ERR_MEMORY_FACT_ORIGIN_MISMATCH': 8044,
    'ERR_MEMORY_FACT_PURGED': 8045,
    'ERR_SYNC_EVENT_CONFLICT': 8041,
}

#: 云端判定为「永久拒绝」的码；daemon 须丢弃 outbox 行并停止重推。
MEMORY_FACT_PERMANENT_CODES = frozenset({8043, 8044, 8045})


# ======================================================================================
# 三、命名空间与主体（与既有下行 parse_semantic_fact_payload 同一套选择规则）
# ======================================================================================

_VALID_SUBJECT_KINDS = frozenset({'owner', 'agent_self', 'peer', 'world'})
_VALID_SCOPE_KINDS = frozenset({'global', 'workspace', 'project', 'task', 'conversation', 'topic'})
_VALID_STATUSES = frozenset({'active', 'superseded', 'disputed', 'withdrawn'})
_VALID_ORIGIN_KINDS = frozenset({'node', 'merged', 'retired'})
_VALID_VERDICTS = frozenset({'merged_into', 'disputed'})

#: 下行事件名（**不等于**上行事件名）：本地 ``memory_restore.rs`` 只认这四个 fact 类型。
_DOWNLINK_FACT_EVENT = {
    'owner': 'memory.owner_fact.upserted',
    'agent_self': 'memory.agent_self_fact.upserted',
    'peer': 'memory.peer_fact.upserted',
    'world': 'memory.world_fact.upserted',
}
#: purge 的下行事件沿用上行同名——它不是 upsert，语义独立。
DOWNLINK_PURGE_EVENT = 'memory.fact.purged'

_FACT_COLUMNS = (
    'fact_id',
    'owner_id',
    'agent_id',
    'subject_kind',
    'subject_id',
    'memory_layer',
    'scope_kind',
    'scope_id',
    'predicate',
    'object_json',
    'confidence',
    'status',
    'superseded_by',
    'source_turn_ids',
    'source_refs_json',
    'rationale',
    'created_at',
    'updated_at',
    'origin_kind',
    'origin_node_id',
    'origin_agent_id',
    'merged_from',
    'revision',
    'merge_verdict',
    'merge_verdict_run',
    'merge_judged_revision',
    'valid_until',
    # doc19 §4.3 / §8.2 双端列：本地 `SemanticFactRecord.supersedes_hint` ↔ 本列。
    'supersedes_hint',
)
# 逐列写死而不是 ``SELECT *`` / ORM 全列展开：本表正被并行切片增列（如 supersedes_hint），
# 模型先于迁移落地时全列展开会整条炸掉；显式列表让本模块与那条时序解耦。
_FACT_SELECT = f'SELECT {", ".join(_FACT_COLUMNS)} FROM hasn_memory.semantic_fact'


def namespace_for(subject_kind: str, agent_id: str | None, owner_id: str) -> tuple[str, str, str]:
    """按主体决定同步命名空间三元组 ``(sync_scope_kind, sync_scope_id, namespace)``。

    规则与既有下行 ``memory_restore.rs::parse_semantic_fact_payload`` **逐字一致**：
    ``agent_self`` → ``agent`` 作用域 + ``agent_facts``；其余（owner/peer/world）→ ``owner``
    作用域 + ``facts``。两边不一致会让回灌事件在客户端 fail-closed 报 owner/namespace 不符。
    """
    if subject_kind == 'agent_self':
        if not agent_id:
            raise _permanent(_PAYLOAD_INVALID_ERROR, 'agent_self 事实缺少 agent_id')
        return 'agent', agent_id, 'agent_facts'
    return 'owner', owner_id, 'facts'


# ======================================================================================
# 四、裁决结果与异常
# ======================================================================================


@dataclass(frozen=True)
class FactUplinkDecision:
    """一条上行事件的处置裁决。

    ``outcome``：

    - ``apply``——正常应用；
    - ``replay``——幂等重放，**视为已应用**（不改库、不推进 namespace_revision）；
    - ``reject_permanent``——重试不会自愈，daemon 丢弃出队 + 一次性 warn；
    - ``reject_conflict``——可退避重试。
    """

    outcome: Literal['apply', 'replay', 'reject_permanent', 'reject_conflict']
    error: ErrorObject | None = None
    reason: str = ''
    #: 已落库的当前行（``apply``/``replay`` 时可用，避免 apply 再查一次）。
    current: Mapping[str, Any] | None = None
    #: 命中跳号（收到的 revision > 现值 + 1）——仍然接受，但要 warn。
    revision_gap: bool = False

    @property
    def accepted(self) -> bool:
        """是否被云端接收（apply 或幂等重放都算接收）。"""
        return self.outcome in {'apply', 'replay'}


class FactUplinkPermanentError(Exception):
    """永久拒绝：载荷非法 / 越权 / 墓碑命中。worker 侧映射为 PermanentSyncBusinessError。"""

    def __init__(self, error: ErrorObject, reason: str) -> None:
        super().__init__(f'{error.name}: {reason}')
        self.error = error
        self.reason = reason


class FactUplinkConflictError(Exception):
    """可重试冲突：事实行尚未汇聚等竞态。daemon 退避重试，worker 走既有重试计数。"""

    def __init__(self, error: ErrorObject, reason: str) -> None:
        super().__init__(f'{error.name}: {reason}')
        self.error = error
        self.reason = reason


def _permanent(error: ErrorObject, reason: str) -> FactUplinkPermanentError:
    return FactUplinkPermanentError(error, reason)


# ======================================================================================
# 五、一次性 warn（毒丸不许每轮重推都刷日志——实施/98 §1.1 的 94% 洪水就是这么来的）
# ======================================================================================

_WARN_ONCE_CAPACITY = 4096
_warned_once: OrderedDict[str, None] = OrderedDict()


def _warn_once(key: str, message: str) -> None:
    """同一 key 只 warn 一次（进程内有界 LRU）。

    进程重启后会再记一条——这是有意的取舍：无界状态换不来更少的日志，而 4096 条水位足以
    覆盖任何一个 owner 的毒丸集合。等级恒为 ``warn``：永久拒绝对云端是 4xx 语义，数据没丢，
    提级成 ``error`` 就是在制造实施/98 同款洪水。
    """
    if key in _warned_once:
        return
    _warned_once[key] = None
    while len(_warned_once) > _WARN_ONCE_CAPACITY:
        _warned_once.popitem(last=False)
    log.warning(message)


def reset_warn_once_cache() -> None:
    """清空一次性 warn 记忆（仅供测试断言「第一次会记、第二次不记」）。"""
    _warned_once.clear()


# ======================================================================================
# 六、载荷解析（schema 非法 → 永久拒绝 8043）
# ======================================================================================


def _text(payload: Mapping[str, Any], key: str, *, max_length: int, required: bool = True) -> str | None:
    value = payload.get(key)
    if value is None or value == '':  # noqa: PLC1901 显式区分「缺字段」与「空串」两种非法输入
        if required:
            raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 必填')
        return None
    if not isinstance(value, str):
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 必须是字符串')
    if len(value) > max_length:
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 长度不能超过 {max_length}')
    return value


def _integer(payload: Mapping[str, Any], key: str, *, required: bool = True) -> int | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 必填')
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 必须是整数')
    return value


def _json_text(value: Any, key: str) -> str:
    """把列表 / 对象等结构化载荷序列化成本表的 JSON 串列（与本地 crate 双端一致）。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 无法序列化为 JSON') from exc


def _string_list(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 必须是字符串数组')
    return list(value)


@dataclass(frozen=True)
class ParsedFact:
    """``saved`` / ``updated`` / ``superseded`` / ``withdrawn`` 的规范化载荷。"""

    fact_id: str
    owner_id: str
    agent_id: str | None
    subject_kind: str
    subject_id: str
    scope_kind: str
    scope_id: str
    predicate: str
    object_json: str
    confidence: float
    status: str
    superseded_by: str | None
    source_turn_ids: str
    source_refs_json: str
    rationale: str | None
    created_at: int
    updated_at: int
    origin_kind: str
    origin_node_id: str | None
    origin_agent_id: str | None
    merged_from: str
    revision: int
    valid_until: int | None
    #: doc19 §4.3 / D-21 本人纠正指向（`save` 携带的线索，非已生效取代关系）。
    supersedes_hint: str | None = None
    #: 原始 object（回灌下行要发回**解析后的值**，不是串）。
    object_value: Any = None


def _enum(payload: Mapping[str, Any], key: str, allowed: frozenset[str], default: str | None = None) -> str:
    """取一个受限枚举列；非法取值一律永久拒绝，**绝不静默回落**到某个默认值。"""
    value = _text(payload, key, max_length=16, required=default is None) or default or ''
    if value not in allowed:
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{key} 取值不受支持：{value}')
    return value


def _is_owner_write(payload: Mapping[str, Any]) -> bool:
    """载荷是否被本地标成「主人整理」（§4.6 第三类写者）。

    **只认布尔真**：字符串 ``'true'``、数字 ``1`` 一律不算。本地构造点写的就是 JSON ``true``，
    宽松解析换不来任何兼容性，只会给伪造多留一条活口。
    """
    return payload.get(OWNER_WRITE_MARKER) is True


def _assert_table_checks(subject_kind: str, scope_kind: str, agent_id: str | None) -> None:
    """预校验表上的两条 CHECK，把 IntegrityError（5xx）提前成明确的永久拒绝（4xx 语义）。"""
    # ck_semantic_fact_agent_id：agent_self 必带 agent_id，其余主体必须为空。
    if subject_kind == 'agent_self' and not agent_id:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'agent_self 事实必须带 agent_id')
    if subject_kind != 'agent_self' and agent_id:
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'{subject_kind} 事实不得带 agent_id')
    # ck_semantic_fact_world_scope：world 主体不许 global 作用域。
    if subject_kind == 'world' and scope_kind == 'global':
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'world 事实不允许 global 作用域')


def _confidence(payload: Mapping[str, Any]) -> float:
    raw = payload.get('confidence')
    try:
        value = float(raw) if raw is not None else 0.6
    except (TypeError, ValueError) as exc:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'confidence 必须是数值') from exc
    return max(0.0, min(1.0, value))


def _parse_fact(payload: Mapping[str, Any], *, owner_id: str) -> ParsedFact:
    """解析一条完整事实快照，并预校验表上的 CHECK，避免 IntegrityError 变成 5xx。"""
    if _text(payload, 'owner_id', max_length=40) != owner_id:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'owner_id 与认证信封不一致')

    subject_kind = _enum(payload, 'subject_kind', _VALID_SUBJECT_KINDS)
    scope_kind = _enum(payload, 'scope_kind', _VALID_SCOPE_KINDS)
    status = _enum(payload, 'status', _VALID_STATUSES, default='active')
    origin_kind = _enum(payload, 'origin_kind', _VALID_ORIGIN_KINDS, default='node')
    agent_id = _text(payload, 'agent_id', max_length=40, required=False)
    _assert_table_checks(subject_kind, scope_kind, agent_id)

    revision = _integer(payload, 'revision')
    if revision is None or revision < 1:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'revision 必须是 >= 1 的整数')

    object_value = payload.get('object_json')

    return ParsedFact(
        fact_id=_text(payload, 'fact_id', max_length=40) or '',
        owner_id=owner_id,
        agent_id=agent_id,
        subject_kind=subject_kind,
        subject_id=_text(payload, 'subject_id', max_length=40) or '',
        scope_kind=scope_kind,
        scope_id=_text(payload, 'scope_id', max_length=40) or '',
        predicate=_text(payload, 'predicate', max_length=4000) or '',
        object_json=_json_text(object_value, 'object_json'),
        confidence=_confidence(payload),
        status=status,
        superseded_by=_text(payload, 'superseded_by', max_length=40, required=False),
        source_turn_ids=_json_text(_string_list(payload, 'source_turn_ids'), 'source_turn_ids'),
        source_refs_json=_json_text(_string_list(payload, 'source_refs'), 'source_refs'),
        rationale=_text(payload, 'rationale', max_length=20000, required=False),
        created_at=_integer(payload, 'created_at') or 0,
        updated_at=_integer(payload, 'updated_at') or 0,
        origin_kind=origin_kind,
        origin_node_id=_text(payload, 'origin_node_id', max_length=64, required=False),
        origin_agent_id=_text(payload, 'origin_agent_id', max_length=40, required=False),
        merged_from=_json_text(_string_list(payload, 'merged_from'), 'merged_from'),
        revision=revision,
        valid_until=_integer(payload, 'valid_until', required=False),
        # 宽度必须与本列 varchar(40) 一致（本地 crate 的 fact_id 同宽）：超长在这里就按
        # 4xx 语义永久拒绝，而不是让它撞进 DB 变成 5xx。
        supersedes_hint=_text(payload, 'supersedes_hint', max_length=40, required=False),
        object_value=object_value,
    )


@dataclass(frozen=True)
class ParsedVerdict:
    """``merge_verdict`` 的规范化载荷——**只有 overlay 三列**，一个业务字段都不接受。"""

    fact_id: str
    merge_verdict: str | None
    merge_verdict_run: str | None
    merge_judged_revision: int | None


def _parse_verdict(payload: Mapping[str, Any]) -> ParsedVerdict:
    verdict = _text(payload, 'merge_verdict', max_length=16, required=False)
    if verdict is not None and verdict not in _VALID_VERDICTS:
        raise _permanent(_PAYLOAD_INVALID_ERROR, f'merge_verdict 取值不受支持：{verdict}')
    return ParsedVerdict(
        fact_id=_text(payload, 'fact_id', max_length=40) or '',
        merge_verdict=verdict,
        merge_verdict_run=_text(payload, 'merge_verdict_run', max_length=40, required=False),
        merge_judged_revision=_integer(payload, 'merge_judged_revision', required=False),
    )


@dataclass(frozen=True)
class ParsedPurge:
    """``purged`` 的规范化载荷。**绝不含被删事实的任何内容**（§4.5）。"""

    fact_id: str
    owner_id: str
    purged_by: str
    reason: str | None


def _parse_purge(payload: Mapping[str, Any], *, owner_id: str) -> ParsedPurge:
    payload_owner = _text(payload, 'owner_id', max_length=40, required=False) or owner_id
    if payload_owner != owner_id:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'owner_id 与认证信封不一致')
    purged_by = _text(payload, 'purged_by', max_length=40) or ''
    # §4.1 权限矩阵：**硬删只有主人**。分身只能软删（withdrawn）。
    if purged_by != owner_id:
        raise _permanent(_PAYLOAD_INVALID_ERROR, 'purge 只有主人可发起（purged_by 必须是主人本人）')
    return ParsedPurge(
        fact_id=_text(payload, 'fact_id', max_length=40) or '',
        owner_id=owner_id,
        purged_by=purged_by,
        reason=_text(payload, 'reason', max_length=64, required=False),
    )


# ======================================================================================
# 七、服务
# ======================================================================================
# 回灌唤醒（doc19 §5.3 / D-9「全量回灌所有节点」）
# ======================================================================================


async def bump_memory_invalidate(db: AsyncSession, owner_id: str) -> None:
    """记忆权威变更后，推 ``hasn.sync.invalidate{kind:memory}`` 唤醒该主人的**全部在线节点**。

    没有这一步，回灌事件只会静静躺在 ``hasn_sync_events`` 里：daemon 侧唯一会拉记忆命名空间的
    触发器就是这条 invalidate（`sync_invalidate.rs::reconcile_memory`）与登录时的一次性 pull。
    实测（doc19 多节点 E2E）：node_A 写下一条事实并成功上行后，node_B 直到重新登录才看得到它,
    D-9「每节点都是完整视图」当场落空。

    与 `peer_portrait_service` 同款 best-effort：权威已落库，推送失败不回滚业务写，靠下一次
    写点或重连握手追平。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_MEMORY, bump_owner

        await bump_owner(KIND_MEMORY, db, owner_id)
    except Exception as exc:  # noqa: BLE001 推送失败不该拖垮已落库的权威写
        log.warning(f'记忆回灌 WSPUSH invalidate 失败 owner={owner_id}: {exc}')


@dataclass
class FactUplinkService:
    """语义事实上行事件的云端权威 apply。"""

    #: 延迟构造，避免 import 期把 sync 网关拉进 hasn_memory 的导入图顶层。
    _gateway: Any = field(default=None, repr=False)

    @property
    def gateway(self) -> Any:
        """同步网关（``emit_memory_event`` 推进 namespace revision 并追加下行事件）。"""
        if self._gateway is None:
            from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

            self._gateway = SqlAlchemySyncGateway()
        return self._gateway

    # ---------------------------------------------------------------- 读侧

    async def _read_fact(self, db: AsyncSession, *, owner_id: str, fact_id: str) -> Mapping[str, Any] | None:
        row = (
            await db.execute(
                # 列名来自本模块常量 `_FACT_COLUMNS`，无外部输入拼接。
                sa.text(f'{_FACT_SELECT} WHERE fact_id = :fact_id AND owner_id = :owner_id'),
                {'fact_id': fact_id, 'owner_id': owner_id},
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    # ---------------------------------------------------------------- 裁决

    async def classify(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> FactUplinkDecision:
        """只读裁决一条上行事件（push 端点预检与 worker apply 共用同一份判据，杜绝口径漂移）。

        push 端点在事件**进 inbox 之前**跑一遍：永久拒绝当场回给 daemon，事件根本不入队——
        这是「毒丸不许进队列」的第一道闸。worker apply 再跑一遍，兜住 push→apply 之间的竞态
        （如中途主人执行了 purge）。
        """
        if event_type not in MEMORY_FACT_UPLINK_EVENTS:
            raise _permanent(_PAYLOAD_INVALID_ERROR, f'不是语义事实上行事件：{event_type}')
        try:
            return await self._classify_inner(
                db, owner_id=owner_id, node_id=node_id, event_type=event_type, payload=payload
            )
        except FactUplinkPermanentError as exc:
            return FactUplinkDecision(outcome='reject_permanent', error=exc.error, reason=exc.reason)
        except FactUplinkConflictError as exc:
            return FactUplinkDecision(outcome='reject_conflict', error=exc.error, reason=exc.reason)

    async def _classify_inner(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> FactUplinkDecision:
        fact_id = _text(payload, 'fact_id', max_length=40) or ''

        # ---- §8.3-6 墓碑防复活：离线节点重新上线推来的晚到事件按删除凭证**永久拒绝** ----
        if event_type in _TOMBSTONE_GUARDED_EVENTS and await fact_tombstone_service.is_purged(db, fact_id):
            _warn_once(
                f'tombstone:{owner_id}:{node_id}:{fact_id}',
                f'记忆事实上行命中墓碑，永久拒绝并回令来源节点清理：'
                f'owner={owner_id} node={node_id} fact={fact_id} event={event_type}',
            )
            return FactUplinkDecision(
                outcome='reject_permanent',
                error=_purge_local_error(fact_id),
                reason='fact_purged',
            )

        if event_type == 'memory.fact.purged':
            purge = _parse_purge(payload, owner_id=owner_id)
            if await fact_tombstone_service.is_purged(db, purge.fact_id):
                # 墓碑已在 = purge 已应用过；重复广播是幂等重放，不是拒绝。
                return FactUplinkDecision(outcome='replay', reason='already_purged')
            return FactUplinkDecision(outcome='apply')

        if event_type == 'memory.fact.merge_verdict':
            return await self._classify_verdict(db, owner_id=owner_id, payload=payload)

        parsed = _parse_fact(payload, owner_id=owner_id)
        current = await self._read_fact(db, owner_id=owner_id, fact_id=parsed.fact_id)
        if event_type == 'memory.fact.saved':
            self._assert_saved_origin(parsed, node_id=node_id, owner_id=owner_id)
        elif event_type in _BUSINESS_UPDATE_EVENTS:
            current = self._assert_editable_own_slice(
                current,
                parsed,
                node_id=node_id,
                owner_id=owner_id,
                owner_write=_is_owner_write(payload),
            )
        else:  # pragma: no cover - 白名单已在入口收口
            raise _permanent(_PAYLOAD_INVALID_ERROR, f'未处理的上行事件：{event_type}')

        if current is None:
            return FactUplinkDecision(outcome='apply')
        return self._revision_decision(current, parsed.revision, owner_id=owner_id, node_id=node_id)

    async def _classify_verdict(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        payload: Mapping[str, Any],
    ) -> FactUplinkDecision:
        """裁决 overlay 提交：只判「事实在不在」与「overlay 是否已等值」，不看 origin。

        **合并 overlay 本就要落到他节点的自产片行上**（§3.4 / §8.3-3：下行写自产片行的
        overlay 字段组是合法的），因此这里刻意不做 origin 守卫——加了反而把合并锁死。
        """
        verdict = _parse_verdict(payload)
        current = await self._read_fact(db, owner_id=owner_id, fact_id=verdict.fact_id)
        if current is None:
            # 事实还没汇聚到云端（save 还在路上）——可退避重试，不是永久错误。
            raise FactUplinkConflictError(_CONFLICT_ERROR, 'merge_verdict 指向的事实尚未汇聚')
        if (
            current['merge_verdict'] == verdict.merge_verdict
            and current['merge_verdict_run'] == verdict.merge_verdict_run
            and current['merge_judged_revision'] == verdict.merge_judged_revision
        ):
            return FactUplinkDecision(outcome='replay', reason='overlay_unchanged', current=current)
        return FactUplinkDecision(outcome='apply', current=current)

    def _assert_saved_origin(self, parsed: ParsedFact, *, node_id: str, owner_id: str) -> None:
        """§3.3 / §8.3-3 origin 守卫：节点只能上行**自己的**自产片。

        ⚠️ ``saved`` **不认** ``owner_write`` 豁免（§4.6 只放开「整理」，不放开「铸造」）：主人
        整理走的是 ``update`` / ``supersede`` / ``withdraw``（本地
        ``daemon/domains/memory/facts.rs::build_owner_update`` 只产这三个 op），``saved`` 恒由
        写下它的那个节点发。放开这里等于允许一个节点替另一个节点**新建**自产片，溯源从第一天
        就是假的，后续所有判权都建立在假归属上。
        """
        if parsed.origin_kind != 'node':
            # merged / retired 片只由合并维护（§3.2），任何节点都不得当作自产片上行。
            raise _permanent(_ORIGIN_MISMATCH_ERROR, f'{parsed.origin_kind} 片不允许经节点上行')
        if parsed.origin_node_id != node_id:
            _warn_once(
                f'origin:{owner_id}:{node_id}:{parsed.fact_id}',
                f'记忆事实上行 origin 与推送节点不符，永久拒绝：owner={owner_id} '
                f'node={node_id} fact={parsed.fact_id} origin_node={parsed.origin_node_id}',
            )
            raise _permanent(_ORIGIN_MISMATCH_ERROR, 'saved 事件的 origin_node_id 必须等于推送节点')

    def _assert_editable_own_slice(
        self,
        current: Mapping[str, Any] | None,
        parsed: ParsedFact,
        *,
        node_id: str,
        owner_id: str,
        owner_write: bool = False,
    ) -> Mapping[str, Any]:
        """判权维度按**写者**划分（§4.1 权限矩阵 + §4.6 主人第三类写者），不是一刀切。

        - **分身整理**（无 ``owner_write`` 标记）：只对本节点自产片生效；他节点 / 派生 / 退役片
          一律 8044 永久拒绝；
        - **主人整理**（载荷带 ``owner_write``，§4.6 / D-20）：主人凌驾节点边界——他从设备 X 改
          一条 origin=设备 Y 的事实必须成立，故**跳过 origin 守卫**，改判「这条事实归不归推送方
          主人」。少了这条豁免，主人写他节点事实的上行会吃 8044 永久拒绝：本地已经生效（记忆页
          立即可见、disputed 立即失效），却永远传播不到其余节点，跨设备记忆就此分叉。

        幂等键仍是 ``(owner, fact_id, revision)``，revision 单调守卫（``_revision_decision``）
        对两类写者一视同仁——主人事件不绕过任何幂等/重放判据，只绕过 origin 判据。

        ------------------------------------------------------------------------------
        ``owner_write`` 标记的越权风险评估（结论随代码走，别只写在提交说明里）
        ------------------------------------------------------------------------------

        1. **跨主人：不可能**。``owner_id`` 取自 push 端点的认证信封
           （``sync.py::require_owner_identity``，不读载荷），``_read_fact`` 按 ``owner_id``
           过滤，``_parse_fact`` 另要求载荷 ``owner_id`` 与信封一致。带上标记也只能改到**自己
           名下**的事实。这是云端唯一能强制的边界，下面那条断言把它显式钉住。
        2. **同主人跨节点：确实被放开**——这正是 §4.6 要的语义。一个被攻陷或有 bug 的本地进程
           可以带着标记去改同一主人另一台设备产的事实。云端**分不出**「主人点的」与「进程伪造
           的」：两者都是同一份 owner 凭据下 daemon 发出的同一种事件，上行面没有区分主人与分身
           的第二重凭据（分身自己走的是 MCP 工具面、本地有自产片守卫，但上行统一由 daemon 代
           主人发）。
        3. **残余风险的量级有限**：能伪造这个标记的前提是已经攻陷该主人的某台节点——那时它本
           就能读写该主人本机整片记忆镜像、并以自己的 ``node_id`` 上行任意**新**事实。标记只多
           给出「改同主人他设备事实的业务字段组」这一档，且它：改不动溯源四列
           （``_apply_business`` 的 ``DO UPDATE`` 故意不含 ``origin_*`` / ``merged_from``）、
           绕不过墓碑（§8.3-6）、绕不过 revision 单调守卫，每次改动都会 emit 下行事件而在主人
           记忆页与各节点可见——是可审计的，不是静默的。
        4. **要再收紧只能靠凭据分层**（主人写另发一枚只有 webui 会话拿得到的短票据，云端据票据
           而非载荷判定写者）。那属于认证面的独立议题，不在本切片；在它落地前，云端强制的边界
           就是 owner，本方法只保证 owner 边界不可越。
        """
        if current is None:
            # save 还在路上（同一批次乱序或前一条推送失败），退避重试即可自愈——不是永久错误。
            raise FactUplinkConflictError(_CONFLICT_ERROR, '待整理的事实尚未汇聚到云端')
        if owner_write:
            # 主人只能改自己的记忆——这条**不随节点边界一起松**。`_read_fact` 已按 owner 过滤，
            # 这里再断言一次是结构性冗余：将来谁把读改成不带 owner 过滤，会在这里当场炸，
            # 而不是悄悄变成一条跨主人写。
            if current['owner_id'] != owner_id:
                _warn_once(
                    f'owner_write_foreign:{owner_id}:{node_id}:{parsed.fact_id}',
                    f'主人事件试图整理不属于本人的事实，永久拒绝：owner={owner_id} node={node_id} '
                    f'fact={parsed.fact_id} fact_owner={current["owner_id"]}',
                )
                raise _permanent(_ORIGIN_MISMATCH_ERROR, '主人事件只能整理归属本人的事实（§4.6）')
            return current
        if current['origin_kind'] != 'node' or current['origin_node_id'] != node_id:
            _warn_once(
                f'origin:{owner_id}:{node_id}:{parsed.fact_id}',
                f'节点试图整理非自产片事实，永久拒绝：owner={owner_id} node={node_id} '
                f'fact={parsed.fact_id} origin={current["origin_kind"]}:{current["origin_node_id"]}',
            )
            raise _permanent(_ORIGIN_MISMATCH_ERROR, '只能整理本节点自产片（§4.1 权限矩阵）')
        return current

    def _revision_decision(
        self,
        current: Mapping[str, Any],
        incoming_revision: int,
        *,
        owner_id: str,
        node_id: str,
    ) -> FactUplinkDecision:
        """revision 单调守卫（§3.4：revision 是上行幂等键成分）。

        - 收到的 ``revision <= 现值`` → **重放**，丢弃并 ``warn``（不是 error：数据没丢，
          云端已经是更新的版本，重推是正常的 at-least-once 语义）；
        - 跳号（``> 现值 + 1``）→ **仍然接受**：本地可能有未上行的中间态（离线整理攒了几手），
          拒绝会让那条事实永远推不上来。记 ``warn`` 留痕。
        """
        stored = int(current['revision'])
        fact_id = current['fact_id']
        if incoming_revision <= stored:
            _warn_once(
                f'replay:{owner_id}:{node_id}:{fact_id}:{incoming_revision}',
                f'记忆事实上行 revision 重放，按已应用处置：owner={owner_id} node={node_id} '
                f'fact={fact_id} incoming={incoming_revision} stored={stored}',
            )
            return FactUplinkDecision(outcome='replay', reason='revision_replay', current=current)
        if incoming_revision > stored + 1:
            _warn_once(
                f'gap:{owner_id}:{node_id}:{fact_id}:{incoming_revision}',
                f'记忆事实上行 revision 跳号，仍然接受（本地可能有未上行中间态）：'
                f'owner={owner_id} node={node_id} fact={fact_id} '
                f'incoming={incoming_revision} stored={stored}',
            )
            return FactUplinkDecision(outcome='apply', current=current, revision_gap=True)
        return FactUplinkDecision(outcome='apply', current=current)

    # ---------------------------------------------------------------- 写侧

    async def apply_fact_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """应用一条上行事件（幂等）。返回 ``{'outcome': ..., 'fact_id': ..., ...}``。

        幂等键 ``(owner_id, origin_node_id, fact_id, revision)``（§8.3-1）：无需额外去重表——
        库里那行的 ``revision`` 本身就是水位，``<=`` 即重放。重放**不推进 namespace_revision**，
        因此不会给各节点造出一条无意义的回灌事件。
        """
        decision = await self.classify(
            db, owner_id=owner_id, node_id=node_id, event_type=event_type, payload=payload
        )
        if decision.outcome == 'reject_permanent':
            raise FactUplinkPermanentError(decision.error or _PAYLOAD_INVALID_ERROR, decision.reason)
        if decision.outcome == 'reject_conflict':
            raise FactUplinkConflictError(decision.error or _CONFLICT_ERROR, decision.reason)
        if decision.outcome == 'replay':
            return {'outcome': 'replay', 'reason': decision.reason, 'event_type': event_type}

        if event_type == 'memory.fact.purged':
            result = await self._apply_purge(db, owner_id=owner_id, node_id=node_id, payload=payload)
        elif event_type == 'memory.fact.merge_verdict':
            result = await self._apply_merge_verdict(db, owner_id=owner_id, payload=payload)
        else:
            result = await self._apply_business(
                db, owner_id=owner_id, event_type=event_type, payload=payload, existed=decision.current is not None
            )
        await bump_memory_invalidate(db, owner_id)
        return result

    async def _apply_business(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        existed: bool,
    ) -> dict[str, Any]:
        """写业务字段组 + 溯源列（§3.4 业务字段组的唯一写者是 origin 节点上的分身与主人）。

        ⚠️ **overlay 三列一个都不出现在这条 SQL 里**——它们只由合并写（``_apply_merge_verdict``）。
        字段组写者不相交是「单一写者」不变量的落点，混写就等于把主脑变成业务状态的第二写者
        （doc19 §3.4 明确禁止：错误裁决会把事实移出下一轮重算输入、永久化）。
        """
        parsed = _parse_fact(payload, owner_id=owner_id)
        values = {
            'fact_id': parsed.fact_id,
            'owner_id': parsed.owner_id,
            'agent_id': parsed.agent_id,
            'subject_kind': parsed.subject_kind,
            'subject_id': parsed.subject_id,
            'scope_kind': parsed.scope_kind,
            'scope_id': parsed.scope_id,
            'predicate': parsed.predicate,
            'object_json': parsed.object_json,
            'confidence': parsed.confidence,
            'status': parsed.status,
            'superseded_by': parsed.superseded_by,
            'source_turn_ids': parsed.source_turn_ids,
            'source_refs_json': parsed.source_refs_json,
            'rationale': parsed.rationale,
            'created_at': parsed.created_at,
            'updated_at': parsed.updated_at,
            'origin_kind': parsed.origin_kind,
            'origin_node_id': parsed.origin_node_id,
            'origin_agent_id': parsed.origin_agent_id,
            'merged_from': parsed.merged_from,
            'revision': parsed.revision,
            'valid_until': parsed.valid_until,
            'supersedes_hint': parsed.supersedes_hint,
        }
        landed = (
            await db.execute(
                sa.text(
                    """
                INSERT INTO hasn_memory.semantic_fact (
                    fact_id, owner_id, agent_id, subject_kind, subject_id, memory_layer,
                    scope_kind, scope_id, predicate, object_json, confidence, status,
                    superseded_by, source_turn_ids, source_refs_json, rationale,
                    created_at, updated_at, origin_kind, origin_node_id, origin_agent_id,
                    merged_from, revision, valid_until, supersedes_hint
                ) VALUES (
                    :fact_id, :owner_id, :agent_id, :subject_kind, :subject_id, 'semantic',
                    :scope_kind, :scope_id, :predicate, :object_json, :confidence, :status,
                    :superseded_by, :source_turn_ids, :source_refs_json, :rationale,
                    :created_at, :updated_at, :origin_kind, :origin_node_id, :origin_agent_id,
                    :merged_from, :revision, :valid_until, :supersedes_hint
                )
                -- ⚠️ DO UPDATE 的 SET 里**故意没有**溯源四列与 created_at：
                --    `origin_*` / `merged_from` 只在首次插入时确定，后续整理不得改写归属，
                --    否则「谁有权整理它」会随一次上行悄悄易主（doc19 §3.2 / §4.2 审计可解释）。
                ON CONFLICT (fact_id) DO UPDATE SET
                    predicate = EXCLUDED.predicate,
                    object_json = EXCLUDED.object_json,
                    confidence = EXCLUDED.confidence,
                    status = EXCLUDED.status,
                    superseded_by = EXCLUDED.superseded_by,
                    source_turn_ids = EXCLUDED.source_turn_ids,
                    source_refs_json = EXCLUDED.source_refs_json,
                    rationale = EXCLUDED.rationale,
                    updated_at = EXCLUDED.updated_at,
                    revision = EXCLUDED.revision,
                    valid_until = EXCLUDED.valid_until,
                    -- 本人纠正指向属业务字段组（分身写的线索），整理时随之覆盖。
                    supersedes_hint = EXCLUDED.supersedes_hint,
                    scope_kind = EXCLUDED.scope_kind,
                    scope_id = EXCLUDED.scope_id
                WHERE hasn_memory.semantic_fact.owner_id = EXCLUDED.owner_id
                RETURNING fact_id
                """
                ),
                values,
            )
        ).scalar_one_or_none()
        if landed is None:
            # fact_id 被别的主人占着 → ON CONFLICT 的 owner 守卫让 DO UPDATE 空转。
            # 这是永久性的客户端问题（本地铸 id 撞车），不是云端不变量破坏，故按 4xx 语义拒绝，
            # 而不是让它掉进 5xx / error 日志。
            raise _permanent(_PAYLOAD_INVALID_ERROR, f'fact_id 已被其它主人占用：{parsed.fact_id}')
        await db.flush()
        revision, event_id = await self._emit_fact_downlink(db, owner_id=owner_id, fact_id=parsed.fact_id)
        return {
            'outcome': 'applied',
            'event_type': event_type,
            'fact_id': parsed.fact_id,
            'revision': parsed.revision,
            'inserted': not existed,
            'sync_revision': revision,
            'sync_event_id': event_id,
        }

    async def _apply_merge_verdict(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """只写 overlay 三列（§3.4 的单一写者不变量，本切片的钉子）。

        **绝不触碰业务字段组、绝不推进 revision、绝不动 updated_at**——合并对他节点自产片的
        修改在同步模型里本就无路可落；只写 overlay 才让「下行写自产片行的 overlay」合法
        （§8.3-3）。同理，``merge_judged_revision`` 与当前 revision 不等时该裁决自动作废，
        竞态靠「裁决过期」消解而不是锁（§5.6）。

        ⚠️ 提交者必须是**当前主脑所在节点**、且 ``owner_memory.version`` CAS 通过——这是
        §5.6 的**云端合并闸**，属 S6 切片。本切片不假装已实现，也不悄悄放行整轮语义：这里
        只做单条 overlay 的幂等落库。
        """
        verdict = _parse_verdict(payload)
        await db.execute(
            sa.text(
                """
                UPDATE hasn_memory.semantic_fact
                   SET merge_verdict = :merge_verdict,
                       merge_verdict_run = :merge_verdict_run,
                       merge_judged_revision = :merge_judged_revision
                 WHERE fact_id = :fact_id AND owner_id = :owner_id
                """
            ),
            {
                'fact_id': verdict.fact_id,
                'owner_id': owner_id,
                'merge_verdict': verdict.merge_verdict,
                'merge_verdict_run': verdict.merge_verdict_run,
                'merge_judged_revision': verdict.merge_judged_revision,
            },
        )
        await db.flush()
        revision, event_id = await self._emit_fact_downlink(db, owner_id=owner_id, fact_id=verdict.fact_id)
        return {
            'outcome': 'applied',
            'event_type': 'memory.fact.merge_verdict',
            'fact_id': verdict.fact_id,
            'merge_verdict': verdict.merge_verdict,
            'sync_revision': revision,
            'sync_event_id': event_id,
        }

    async def _apply_purge(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """主人硬删 + 沿 ``merged_from`` 递归级联（§4.5 / D-19）。

        「不留任何内容」必须真做到，因此四件事缺一不可：

        1. 递归物理删所有 ``merged_from`` 含被删 fact 的派生事实（它们的内容可能就含被删信息）；
        2. 每条被删事实各写一条墓碑（**只有 fact_id / owner / 时间 / 发起人 / 级联来源**）；
        3. 登记「画像重算待办」——``owner_memory`` / ``peer_portrait`` 正文可能仍含其内容；
        4. 同事务内先把指向待删行的 ``superseded_by`` 置 NULL（自引用连带修复），**不推进
           revision**：这是 purge 的连带修复，不是业务整理，不该触发上行事件或使 overlay 失效。
        """
        purge = _parse_purge(payload, owner_id=owner_id)
        lineage = await self._collect_purge_lineage(db, owner_id=owner_id, root_fact_id=purge.fact_id)
        if not lineage:
            # 事实本就不在云端（本地删完才汇聚过来）：仍然登记墓碑，否则晚到的 save 会复活它。
            # 此时读不到 subject_kind，广播只能落 owner/facts 命名空间——各节点本就同时订阅
            # `facts` 与 `agent_facts`（`sync_pull.rs`），按 fact_id 删除不受命名空间影响。
            await fact_tombstone_service.record_purge(
                db,
                fact_id=purge.fact_id,
                owner_id=owner_id,
                purged_by=purge.purged_by,
                reason=purge.reason,
            )
            await db.flush()
            await self._emit_purge_downlink(
                db,
                owner_id=owner_id,
                fact_id=purge.fact_id,
                purged_by=purge.purged_by,
                cascade_from=None,
                reason=purge.reason,
                subject_kind='owner',
                agent_id=None,
            )
            return {'outcome': 'applied', 'event_type': 'memory.fact.purged', 'purged': [purge.fact_id]}

        ids = [item['fact_id'] for item in lineage]
        # 4. 自引用连带修复：先断开指向待删行的 superseded_by，再删。不推进 revision。
        #    （云端本表未建自引用外键，本地 SQLite 建了——两端语义必须一致，否则本地删得掉、
        #     云端留一堆悬垂引用，主人记忆页会看到指向虚空的「已被取代」。）
        await db.execute(
            sa.text(
                'UPDATE hasn_memory.semantic_fact SET superseded_by = NULL '
                'WHERE owner_id = :owner_id AND superseded_by IN :ids'
            ).bindparams(sa.bindparam('ids', expanding=True)),
            {'owner_id': owner_id, 'ids': ids},
        )
        await db.execute(
            sa.text(
                'DELETE FROM hasn_memory.semantic_fact WHERE owner_id = :owner_id AND fact_id IN :ids'
            ).bindparams(sa.bindparam('ids', expanding=True)),
            {'owner_id': owner_id, 'ids': ids},
        )
        # 2. 墓碑（含级联来源）。
        for item in lineage:
            await fact_tombstone_service.record_purge(
                db,
                fact_id=item['fact_id'],
                owner_id=owner_id,
                purged_by=purge.purged_by,
                cascade_from=item['cascade_from'],
                reason=purge.reason,
            )
        # 3. 画像重算待办：purge 不能只删事实——合并态正文里可能还留着它的内容。
        #    requested_by_agent 记发起人（purge 只有主人可发起，故这里就是主人 hasn_id）。
        await merge_request_service.request_merge(
            db,
            owner_id=owner_id,
            requested_by_agent=purge.purged_by,
            requested_by_node=node_id,
            reason='purge_cascade',
        )
        await db.flush()
        # 5. 广播下行，让其余节点物理删除同一批事实。
        for item in lineage:
            await self._emit_purge_downlink(
                db,
                owner_id=owner_id,
                fact_id=item['fact_id'],
                purged_by=purge.purged_by,
                cascade_from=item['cascade_from'],
                reason=purge.reason,
                subject_kind=item['subject_kind'],
                agent_id=item['agent_id'],
            )
        return {
            'outcome': 'applied',
            'event_type': 'memory.fact.purged',
            'purged': ids,
            'cascade': [item['fact_id'] for item in lineage if item['cascade_from'] is not None],
        }

    async def _collect_purge_lineage(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        root_fact_id: str,
    ) -> list[dict[str, Any]]:
        """BFS 展开血缘闭包：根事实 + 所有（递归）以它为血缘祖先的派生事实。

        ``merged_from`` 是 JSON 串列（与本地 crate 双端一致），先用 ``LIKE`` 预筛再在 Python 侧
        做精确成员判定——不依赖 JSON 函数对该列的可解析性（历史脏数据可能不是合法 JSON）。
        事实量级 10²–10⁴，一次 owner 级 BFS 无压力。
        """
        root = await self._read_fact(db, owner_id=owner_id, fact_id=root_fact_id)
        if root is None:
            return []
        lineage: list[dict[str, Any]] = [
            {
                'fact_id': root['fact_id'],
                'cascade_from': None,
                'subject_kind': root['subject_kind'],
                'agent_id': root['agent_id'],
            }
        ]
        seen = {root['fact_id']}
        frontier = [root['fact_id']]
        while frontier:
            parent = frontier.pop()
            rows = (
                await db.execute(
                    sa.text(
                        'SELECT fact_id, merged_from, subject_kind, agent_id '
                        'FROM hasn_memory.semantic_fact '
                        "WHERE owner_id = :owner_id AND merged_from LIKE '%' || :needle || '%'"
                    ),
                    {'owner_id': owner_id, 'needle': parent},
                )
            ).mappings().all()
            for row in rows:
                if row['fact_id'] in seen:
                    continue
                try:
                    parsed_lineage = json.loads(row['merged_from'] or '[]')
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(parsed_lineage, list) or parent not in parsed_lineage:
                    continue
                seen.add(row['fact_id'])
                lineage.append(
                    {
                        'fact_id': row['fact_id'],
                        'cascade_from': parent,
                        'subject_kind': row['subject_kind'],
                        'agent_id': row['agent_id'],
                    }
                )
                frontier.append(row['fact_id'])
        return lineage

    # ---------------------------------------------------------------- 下行广播

    async def emit_fact_downlink(self, db: AsyncSession, *, owner_id: str, fact_id: str) -> tuple[int, str]:
        """对外的回灌发射口：合并闸（§5.6）写完 overlay / 派生事实后调它广播给各节点。

        刻意只做转发、不复制实现——「回灌不得抹平溯源」（§8.2）的全列回发规则只能有一处，
        合并闸另写一份迟早漏列，那正是自产片归属被抹平、事实从此不可整理的事故形态。
        """
        return await self._emit_fact_downlink(db, owner_id=owner_id, fact_id=fact_id)

    async def _emit_fact_downlink(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        fact_id: str,
    ) -> tuple[int, str]:
        """推进 namespace revision 并追加一条回灌事件，让其余节点拿到新版本。

        **溯源与 overlay 六列必须完整回发**（§8.2「回灌不得抹平溯源」）：来源节点自己也会按
        游标把这条事件拉回去，下行 applier 走 ``ON CONFLICT DO UPDATE``；若回发的载荷缺
        ``origin_*``，该节点自产片行的归属会被抹平、从此不可整理。这不是理论风险，是 S5
        的必查项，故此处一律从**刚落库的行**重新读出全量列再发。

        下行事件名用既有的 ``memory.{subject}_fact.upserted``（本地 ``memory_restore.rs`` 只认
        这四个），**不是**上行事件名——上下行是两套命名，混用会让回灌被静默丢弃。
        """
        row = await self._read_fact(db, owner_id=owner_id, fact_id=fact_id)
        if row is None:
            # 落库后立刻读不到 = 不变量破坏（同事务内），这才是 error 级。
            log.error(f'记忆事实上行落库后读不到该行，回灌无法发出：owner={owner_id} fact={fact_id}')
            raise RuntimeError(f'semantic_fact_vanished_after_apply:{fact_id}')
        scope_kind, scope_id, namespace = namespace_for(row['subject_kind'], row['agent_id'], owner_id)
        payload = {
            'fact_id': row['fact_id'],
            'owner_id': row['owner_id'],
            'agent_id': row['agent_id'],
            'subject_kind': row['subject_kind'],
            'subject_id': row['subject_id'],
            'scope_kind': row['scope_kind'],
            'scope_id': row['scope_id'],
            'predicate': row['predicate'],
            'object_json': _safe_loads(row['object_json']),
            'confidence': row['confidence'],
            'status': row['status'],
            'superseded_by': row['superseded_by'],
            'source_turn_ids': _safe_loads(row['source_turn_ids']) or [],
            'source_refs': _safe_loads(row['source_refs_json']) or [],
            'rationale': row['rationale'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            # doc19 §4.3 本人纠正指向（双端列）：回灌必须带回去，否则来源节点自己按游标
            # 拉回这条事件时会拿 payload 覆盖本地行、把 hint 抹平——与溯源同款风险。
            'supersedes_hint': row['supersedes_hint'],
            # ---- §8.2 溯源与 overlay 六列：一个都不许少 ----
            'origin_kind': row['origin_kind'],
            'origin_node_id': row['origin_node_id'],
            'origin_agent_id': row['origin_agent_id'],
            'merged_from': _safe_loads(row['merged_from']) or [],
            'revision': row['revision'],
            'merge_verdict': row['merge_verdict'],
            'merge_verdict_run': row['merge_verdict_run'],
            'merge_judged_revision': row['merge_judged_revision'],
            'valid_until': row['valid_until'],
        }
        return await self.gateway.emit_memory_event(
            db,
            owner_id=owner_id,
            event_type=_DOWNLINK_FACT_EVENT[row['subject_kind']],
            namespace=namespace,
            aggregate_id=row['fact_id'],
            payload=payload,
            sync_scope_kind=scope_kind,
            sync_scope_id=scope_id,
            hasn_id=row['agent_id'] or owner_id,
        )

    async def _emit_purge_downlink(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        fact_id: str,
        purged_by: str,
        cascade_from: str | None,
        reason: str | None,
        subject_kind: str,
        agent_id: str | None,
    ) -> tuple[int, str]:
        """广播一条 purge 指令。**载荷绝不含被删事实的任何内容**（§4.5）。"""
        scope_kind, scope_id, namespace = namespace_for(subject_kind, agent_id, owner_id)
        return await self.gateway.emit_memory_event(
            db,
            owner_id=owner_id,
            event_type=DOWNLINK_PURGE_EVENT,
            namespace=namespace,
            aggregate_id=fact_id,
            payload={
                'fact_id': fact_id,
                'purged_by': purged_by,
                'cascade_from': cascade_from,
                'reason': reason,
                'purged_at': int(time() * 1000),
            },
            sync_scope_kind=scope_kind,
            sync_scope_id=scope_id,
            hasn_id=agent_id or owner_id,
        )


def _purge_local_error(fact_id: str) -> ErrorObject:
    """墓碑命中的拒绝对象——``detail.action='purge_local'`` 是回令来源节点清理的明确指令。

    daemon 收到后应：删本地行 + 删该 fact 的全部残留 outbox op，然后**停止重推**（§4.5 墓碑
    防复活）。``client_event_id`` 由 push 端点在返回前补进同一个 ``detail``。
    """
    return _PURGED_ERROR.model_copy(
        update={'detail': {'action': 'purge_local', 'fact_id': fact_id}}
    )


def _safe_loads(raw: Any) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw  # 历史脏数据原样回发，不崩


def bind_client_event_id(error: ErrorObject, client_event_id: str) -> ErrorObject:
    """把拒绝对象绑定到具体事件，保留已有 ``detail``（如 ``purge_local`` 指令）。

    实施/98 D-3：``detail.client_event_id`` 是 daemon 逐事件定位的唯一锚点；丢了它就退回
    「一条毒丸拖住整批」的老路。
    """
    detail = dict(error.detail or {})
    detail['client_event_id'] = client_event_id
    return error.model_copy(update={'detail': detail})


fact_uplink_service: FactUplinkService = FactUplinkService()
