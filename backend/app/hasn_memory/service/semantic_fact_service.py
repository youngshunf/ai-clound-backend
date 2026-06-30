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
- 只读方法默认只取 `status='active'`，被替代/撤回的事实不喂召回。
"""
from __future__ import annotations

import json
import uuid

from time import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_memory.model.semantic_fact import SemanticFact

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_VALID_SUBJECT_KINDS = frozenset({'owner', 'agent_self', 'peer', 'world'})
_VALID_SCOPE_KINDS = frozenset({'global', 'workspace', 'project', 'task', 'conversation', 'topic'})
_DEFAULT_CONFIDENCE = 0.6
_MAX_LIMIT = 200


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
        'object': _loads(fact.object_json),
        'confidence': fact.confidence,
        'status': fact.status,
        'rationale': fact.rationale,
        'created_at': fact.created_at,
        'updated_at': fact.updated_at,
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
    ) -> dict[str, Any]:
        """落一条语义事实（云端权威）。owner_id 由凭据强制，绝不取自请求体。"""
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
            source_refs_json=_dumps(source_refs or []),
            rationale=rationale,
            created_at=now,
            updated_at=now,
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
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """按文本在 predicate/object_json 上模糊搜索 owner 名下 active 事实。"""
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            SemanticFact.status == 'active',
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
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
        scope_kind: str | None = None,
        scope_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """召回某主体/作用域下的 active 事实（供注入），按 updated_at 倒序。"""
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            SemanticFact.status == 'active',
            SemanticFact.confidence >= float(min_confidence),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
        if subject_id:
            stmt = stmt.where(SemanticFact.subject_id == subject_id)
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
        """分页列 owner 名下 active 事实（默认全主体；可按 subject_kind / agent_id 过滤）。"""
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        offset = max(0, int(offset))
        base = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            SemanticFact.status == 'active',
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            base = base.where(SemanticFact.subject_kind == subject_kind)
        if agent_id:
            base = base.where(SemanticFact.agent_id == agent_id)
        total = (await db.execute(sa.select(sa.func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            await db.execute(base.order_by(SemanticFact.updated_at.desc()).limit(limit).offset(offset))
        ).scalars().all()
        return {'total': total, 'items': [_serialize(r) for r in rows], 'limit': limit, 'offset': offset}


semantic_fact_service = SemanticFactService()
