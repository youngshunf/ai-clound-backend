"""记忆云端测试共用的真实事实上行造数助手。"""

from __future__ import annotations

import time
import uuid

from itertools import count
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn_memory.model.semantic_fact import SemanticFact
from backend.app.hasn_memory.service.fact_uplink_service import fact_uplink_service
from backend.app.hasn_memory.service.semantic_fact_service import _serialize

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_CLOCK_TICK = count()


async def seed_local_fact_uplink(
    db: AsyncSession,
    *,
    owner_id: str,
    agent_id: str,
    subject_kind: str = 'agent_self',
    subject_id: str | None = None,
    predicate: str,
    object_value: Any,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    fact_id: str | None = None,
    node_id: str | None = None,
    revision: int = 1,
    confidence: float = 0.8,
    status: str = 'active',
    superseded_by: str | None = None,
    rationale: str | None = None,
    valid_until: int | None = None,
    supersedes_hint: str | None = None,
    origin_kind: str = 'node',
    origin_node_id: str | None = None,
    origin_agent_id: str | None = None,
    merged_from: list[str] | None = None,
    **_ignored: object,
) -> dict[str, Any]:
    """从唯一允许的 ``memory.fact.saved`` 上行入口落一条真实云端镜像。"""
    resolved_subject_id = subject_id
    if not resolved_subject_id:
        if subject_kind == 'owner':
            resolved_subject_id = owner_id
        elif subject_kind == 'agent_self':
            resolved_subject_id = agent_id
        else:
            resolved_subject_id = f'{subject_kind}:{uuid.uuid4().hex[:16]}'

    resolved_scope_kind = scope_kind or 'global'
    resolved_scope_id = scope_id or resolved_subject_id

    resolved_fact_id = fact_id or uuid.uuid4().hex
    resolved_node_id = node_id or origin_node_id or f'node_{uuid.uuid4().hex[:12]}'
    updated_at = int(time.time() * 1000) + next(_CLOCK_TICK)
    payload = {
        'fact_id': resolved_fact_id,
        'owner_id': owner_id,
        'agent_id': agent_id if subject_kind == 'agent_self' else None,
        'subject_kind': subject_kind,
        'subject_id': resolved_subject_id,
        'scope_kind': resolved_scope_kind,
        'scope_id': resolved_scope_id,
        'predicate': predicate,
        'object_json': object_value,
        'confidence': confidence,
        'status': status,
        'superseded_by': superseded_by,
        'source_turn_ids': [],
        'source_refs': [],
        'rationale': rationale,
        'valid_until': valid_until,
        'supersedes_hint': supersedes_hint or None,
        'created_at': updated_at,
        'updated_at': updated_at,
        'revision': revision,
        'origin_kind': origin_kind,
        'origin_node_id': origin_node_id or resolved_node_id,
        'origin_agent_id': origin_agent_id or agent_id,
        'merged_from': merged_from or [],
    }
    await fact_uplink_service.apply_fact_event(
        db,
        owner_id=owner_id,
        node_id=resolved_node_id,
        event_type='memory.fact.saved',
        payload=payload,
    )
    await db.commit()
    row = (
        await db.execute(
            select(SemanticFact).where(SemanticFact.fact_id == resolved_fact_id)
        )
    ).scalar_one()
    return _serialize(row)
