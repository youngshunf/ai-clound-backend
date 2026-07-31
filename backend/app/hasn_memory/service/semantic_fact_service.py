"""语义事实云端权威服务（doc16 Phase C：单一云端记忆）。

承接 `hasn.memory.{save,search,recall,list}` 平台工具的云端权威读写：分身（云端 / 本地
runtime 统一）经 `/api/v1/mcp/streamable` 直达云端，工具体 in-process 直调本服务，读写
`hasn_memory.semantic_fact`（四主体四层记忆线之 semantic 语义层云端权威表）。

设计要点（与 doc16 §10 强约束对齐）：
- **owner 隔离强制**：所有读写以 `owner_id`（Agent JWT/MCP Key 解析出的 owner_hasn_id）为
  硬边界，身份绝不入请求体。
- **主体规约**：`agent_self` 必带 `agent_id` 且 `subject_id == agent_id`；`owner` 的
  `subject_id == owner_id`；`peer`/`world` 由调用方给 `subject_id`（agent_id 必空）。
  与表 CHECK 约束（`ck_semantic_fact_agent_id` / `ck_semantic_fact_world_scope`）一致。
- **置信度闸**：confidence 落 [0,1]，缺省 0.6；低于阈值的写入仍落库但标 confidence（召回侧
  可按阈值过滤）。
- **object 串字段**：与本地 crate 双端一致，`object_json` 存 JSON 字符串；读出反序列化为
  `object`。时间 epoch ms（与本地 BigInteger 一致）。
- 只读方法默认只取**生效事实**（doc19 §3.4，见 `effective_fact_condition`），被替代/撤回的
  事实与「裁决仍然有效的」被合并/矛盾事实都不喂召回。

doc19 S3（19-多节点记忆分层与分身自治整理设计.md §3.2/§3.4/§8.2）：
- `save_fact` 写入时落 `origin_kind='node'` / `revision=1`，并接收 `origin_node_id`、
  `origin_agent_id`、`valid_until`、`supersedes_hint`——溯源与时效**先落库**，
  裁决逻辑（hint 快速通道、overlay 写入）在 S6 合并里做，本片不假装已实现；
- 生效可见性 = `status='active'` 且（未裁决 或 裁决所依据的 revision 已过期）。
  后半句是并发护栏：事实被裁决后又被本地整理或主人修改（revision 前进），旧裁决自动失效、
  事实重新可见，留待下轮合并重裁——竞态从「谁覆盖谁」变成「裁决过期作废」，不需要锁。
"""

from __future__ import annotations

import json
import uuid

from time import time
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa

from backend.app.hasn_memory.model.semantic_fact import SemanticFact

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement

_VALID_SUBJECT_KINDS = frozenset({'owner', 'agent_self', 'peer', 'world'})
_VALID_SCOPE_KINDS = frozenset({'global', 'workspace', 'project', 'task', 'conversation', 'topic'})
_DEFAULT_CONFIDENCE = 0.6
_MAX_LIMIT = 200


def effective_fact_condition() -> ColumnElement[bool]:
    """生效可见性判据（doc19 §3.4，注入 / 检索默认过滤）。

    ``status='active'`` 且（``merge_verdict IS NULL`` 或 ``merge_judged_revision`` 与当前
    ``revision`` 不同）。

    后半句是**并发护栏**，不是可选优化：合并把某条标成 `merged_into` / `disputed` 之后，
    该事实又被 origin 节点的分身整理、或被主人确认（§4.6，主人写使 revision 前进），旧裁决
    **自动失效**、事实立即重新可见，留待下轮合并重裁。「合并 vs 本地整理」的竞态因此从
    「谁覆盖谁」退化成「裁决过期作废」，不需要任何锁。

    ``IS DISTINCT FROM`` 而非 ``!=``：`merge_judged_revision` 可空，SQL 三值逻辑下
    ``NULL != 1`` 求值为 NULL（该行会被整体过滤掉），只有 IS DISTINCT FROM 才把
    「有裁决但没记依据 revision」的行按「裁决不可信 → 事实可见」处理。
    """
    return sa.and_(
        SemanticFact.status == 'active',
        sa.or_(
            SemanticFact.merge_verdict.is_(None),
            SemanticFact.merge_judged_revision.is_distinct_from(SemanticFact.revision),
        ),
    )


def _now_ms() -> int:
    return int(time() * 1000)


def _gen_fact_id() -> str:
    """云端直接铸造的 fact_id（不依赖 ulid 库；与本地 crate 同为不透明字符串，varchar(40) 内）。"""
    return uuid.uuid4().hex


def _clamp_confidence(value: Any) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, c))


def _dumps(value: Any) -> str:
    """object → JSON 字符串。已是字符串则按「标量字符串」包成 JSON（保持 object_json 永远是合法 JSON）。"""
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw  # 历史脏数据：原样返回，不崩


def _serialize(fact: SemanticFact) -> dict[str, Any]:
    return {
        'fact_id': fact.fact_id,
        'owner_id': fact.owner_id,
        'agent_id': fact.agent_id,
        'subject_kind': fact.subject_kind,
        'subject_id': fact.subject_id,
        'scope_kind': fact.scope_kind,
        'scope_id': fact.scope_id,
        'predicate': fact.predicate,
        'object': _loads(cast(str, fact.object_json)),
        'confidence': fact.confidence,
        'status': fact.status,
        'rationale': fact.rationale,
        'created_at': fact.created_at,
        'updated_at': fact.updated_at,
        # doc19 §3.2/§3.4：溯源与裁决 overlay 随读返回——分身据此一眼看出哪些它能改（§7.2 editable）
        'origin_kind': fact.origin_kind,
        'origin_node_id': fact.origin_node_id,
        'origin_agent_id': fact.origin_agent_id,
        'revision': fact.revision,
        'merge_verdict': fact.merge_verdict,
        'merge_verdict_run': fact.merge_verdict_run,
        'merge_judged_revision': fact.merge_judged_revision,
        'valid_until': fact.valid_until,
    }


class SemanticFactService:
    """语义事实云端权威 CRUD（owner 隔离强制）。"""

    @staticmethod
    async def save_fact(
        db: AsyncSession,
        *,
        owner_id: str,
        agent_id: str | None,
        predicate: str,
        object_value: Any,
        subject_kind: str = 'agent_self',
        subject_id: str | None = None,
        confidence: Any = None,
        scope_kind: str = 'global',
        scope_id: str | None = None,
        source_refs: list[Any] | None = None,
        rationale: str | None = None,
        origin_node_id: str | None = None,
        origin_agent_id: str | None = None,
        valid_until: int | None = None,
        supersedes_hint: str | None = None,
    ) -> dict[str, Any]:
        """落一条语义事实（云端权威）。owner_id 由凭据强制，绝不取自请求体。

        doc19 §3.2/§4.3 新增入参：

        - `origin_node_id` / `origin_agent_id`：溯源。**由服务端按凭据强制写入**，不接受调用
          方（分身）自行指定；缺省留空表示「产自未知节点」，本地判自产片时不会误判成自产；
        - `valid_until`：时效性事实的有效期截止（epoch ms），review 复盘候选③依据；
        - `supersedes_hint`：本人纠正旧事实的快速通道线索（§4.3）。本片**只落库**——新事实
          照常进本节点自产片，hint 的裁决（同 `origin_agent_id` 即按 hint 直接裁决、指向他人
          事实只作 LLM 参考信号）属 S6 合并，此处不做任何隐式取代，免得假装已经生效。
        """
        if not predicate or not str(predicate).strip():
            raise ValueError('predicate 必填')
        subject_kind = subject_kind if subject_kind in _VALID_SUBJECT_KINDS else 'agent_self'
        scope_kind = scope_kind if scope_kind in _VALID_SCOPE_KINDS else 'global'

        # 主体规约（与表 CHECK 一致）：agent_self→subject_id=agent_id 且 agent_id 必填；
        # owner→subject_id=owner_id；peer/world→给定 subject_id，agent_id 必空。
        row_agent_id: str | None = None
        if subject_kind == 'agent_self':
            if not agent_id:
                raise ValueError('agent_self 事实需要 agent_id（凭据缺失）')
            row_agent_id = agent_id
            resolved_subject_id = agent_id
        elif subject_kind == 'owner':
            resolved_subject_id = owner_id
        else:  # peer / world
            resolved_subject_id = subject_id or ''
            if not resolved_subject_id:
                raise ValueError(f'{subject_kind} 事实需要 subject_id')
        # world 不允许 global 作用域（表 CHECK ck_semantic_fact_world_scope）
        if subject_kind == 'world' and scope_kind == 'global':
            scope_kind = 'topic'

        # doc19 §4.3：hint 只是「本人纠正本人」的线索，随来源引用一起留痕，交给 S6 合并裁决。
        # 现表无独立列承载它（§8.2 增列汇总未列出），故落在 source_refs_json 的结构化条目里，
        # 语义显式（kind='supersedes_hint'），不挤占 superseded_by——后者是已生效的取代关系。
        resolved_source_refs: list[Any] = list(source_refs or [])
        if supersedes_hint:
            resolved_source_refs.append({'kind': 'supersedes_hint', 'fact_id': supersedes_hint})

        now = _now_ms()
        fact = SemanticFact(
            fact_id=_gen_fact_id(),
            owner_id=owner_id,
            agent_id=row_agent_id,
            subject_kind=subject_kind,
            subject_id=resolved_subject_id,
            memory_layer='semantic',
            scope_kind=scope_kind,
            scope_id=scope_id or resolved_subject_id or 'global',
            predicate=str(predicate).strip(),
            object_json=_dumps(object_value),
            confidence=_clamp_confidence(confidence if confidence is not None else _DEFAULT_CONFIDENCE),
            status='active',
            source_turn_ids='[]',
            source_refs_json=_dumps(resolved_source_refs),
            rationale=rationale,
            created_at=now,
            updated_at=now,
            # doc19 §3.2：新写入的事实一律是「节点自产」，血缘为空；overlay 三列留 NULL——
            # 只有合并能写（§3.4 字段组写者不相交），save 永远不碰它们。
            origin_kind='node',
            origin_node_id=origin_node_id,
            origin_agent_id=origin_agent_id,
            merged_from='[]',
            revision=1,
            valid_until=valid_until,
        )
        db.add(fact)
        await db.flush()
        return _serialize(fact)

    @staticmethod
    async def search_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        query: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """按文本在 predicate/object_json 上模糊搜索 owner 名下**生效**事实。

        doc18 S2：可选 `subject_id` 限定主体——「按人 + 关键词」组合检索（如查某 peer 关于某话题的事实）。
        doc19 §3.4：生效判据含合并裁决 overlay，裁决过期即自动重新可见。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
        if subject_id:
            stmt = stmt.where(SemanticFact.subject_id == subject_id)
        q = (query or '').strip()
        if q:
            like = f'%{q}%'
            stmt = stmt.where(sa.or_(SemanticFact.predicate.ilike(like), SemanticFact.object_json.ilike(like)))
        stmt = stmt.order_by(SemanticFact.updated_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize(r) for r in rows]

    @staticmethod
    async def recall_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        query: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """召回某主体/作用域下的**生效**事实（供注入），按 updated_at 倒序。

        doc18 S2：可选 `query` 词项过滤（predicate/object ILIKE，与 search 同实现）——补上「按人 + 关键词」召回。
        doc19 §3.4：被合并裁决为 merged_into / disputed 且裁决仍有效的事实**不进注入**。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
            SemanticFact.confidence >= float(min_confidence),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
        if subject_id:
            stmt = stmt.where(SemanticFact.subject_id == subject_id)
        q = (query or '').strip()
        if q:
            like = f'%{q}%'
            stmt = stmt.where(sa.or_(SemanticFact.predicate.ilike(like), SemanticFact.object_json.ilike(like)))
        if scope_kind in _VALID_SCOPE_KINDS:
            stmt = stmt.where(SemanticFact.scope_kind == scope_kind)
        if scope_id:
            stmt = stmt.where(SemanticFact.scope_id == scope_id)
        stmt = stmt.order_by(SemanticFact.updated_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize(r) for r in rows]

    @staticmethod
    async def list_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        subject_kind: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列 owner 名下**生效**事实（默认全主体；可按 subject_kind / agent_id 过滤）。

        doc19 §3.4：与 search/recall 同一判据，避免「列表里有、召回里没有」的口径分裂。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        offset = max(0, int(offset))
        base = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            base = base.where(SemanticFact.subject_kind == subject_kind)
        if agent_id:
            base = base.where(SemanticFact.agent_id == agent_id)
        total = (await db.execute(sa.select(sa.func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (await db.execute(base.order_by(SemanticFact.updated_at.desc()).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return {'total': total, 'items': [_serialize(r) for r in rows], 'limit': limit, 'offset': offset}


semantic_fact_service = SemanticFactService()
