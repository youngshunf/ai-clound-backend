"""Peer 画像下行发射真实 PG 验收（doc17 PEERSYN-P3，零 mock）。

验证合成后的下行链路（G4/G6）：
- `sweep_peer_portraits` 合成成功后经 `_emit_peer_portrait_event` →
  `hasn_sync_service.gateway.emit_memory_event` 往 `public.hasn_sync_events` 写一条
  `memory.peer_portrait.upserted`（namespace='portraits'）；
- payload 字段名**严格对齐 daemon `parse_peer_portrait_payload` 契约**：正文键 `portrait`（非
  portrait_text）、`peer_hasn_id`、`sync_scope_kind='owner'`、`sync_scope_id=owner`、
  `namespace='portraits'`、`record_id=peer`、`namespace_revision`（单调 > 0）、
  `created_at`/`updated_at`（epoch ms int）；
- 该事件可被 `pull_memory_events`（namespace 游标增量）拉取到 → 证明 daemon 端能拉。

LLM 用 service 既有注入式 ``llm_complete`` 打桩（sanctioned 注入点，非业务 mock）。需本地
PostgreSQL :15432（否则 skip）。
"""

from __future__ import annotations

import json
import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_sync import MemorySyncCursor
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_memory.model.peer_portrait import PeerPortrait
from backend.app.hasn_memory.model.semantic_fact import SemanticFact
from backend.app.hasn_memory.service.peer_portrait_service import peer_portrait_service
from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        # sweep 内部用全局 async_db_session；pytest-asyncio 每测试新事件循环 → 释放全局池避免串循环。
        await async_engine.dispose()


def _echo_llm() -> Callable[[list[dict[str, str]]], Awaitable[str]]:
    async def _complete(messages: list[dict[str, str]]) -> str:  # noqa: RUF029 — async 匹配 Awaitable 协议
        user = next((m['content'] for m in messages if m['role'] == 'user'), '')
        return '画像｜' + user.replace('\n', ' ')

    return _complete


async def _seed_peer_fact(session: AsyncSession, owner_id: str, peer_hasn_id: str, *, predicate: str, obj: str) -> None:
    await semantic_fact_service.save_fact(
        session,
        owner_id=owner_id,
        agent_id=None,
        subject_kind='peer',
        subject_id=peer_hasn_id,
        predicate=predicate,
        object_value=obj,
        confidence=0.8,
    )
    await session.commit()


async def _cleanup(session: AsyncSession, owner_id: str) -> None:
    # 测试脚手架 owner（随机 uuid），清干净：画像/事实/下行事件/namespace 游标。
    await session.execute(delete(PeerPortrait).where(PeerPortrait.owner_id == owner_id))
    await session.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner_id))
    await session.execute(
        sa.text('DELETE FROM public.hasn_sync_events WHERE owner_id = :o'), {'o': owner_id}
    )
    await session.execute(
        sa.text('DELETE FROM hasn_memory.namespace_revision WHERE sync_scope_id = :o'), {'o': owner_id}
    )
    await session.commit()


async def test_sweep_emits_peer_portrait_downlink_event(session: AsyncSession) -> None:
    """合成成功 → 写一条 memory.peer_portrait.upserted，payload 对齐 daemon 契约，且可拉取。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    await _seed_peer_fact(session, owner, peer, predicate='职业', obj='投资人')
    await _seed_peer_fact(session, owner, peer, predicate='沟通偏好', obj='喜欢简洁直接')

    try:
        summary = await peer_portrait_service.sweep_peer_portraits(owner_ids=[owner], llm_complete=_echo_llm())
        assert summary['synthesized'] >= 1

        # 1) hasn_sync_events 落了一条下行事件
        row = (
            await session.execute(
                sa.text(
                    """
                    SELECT event_type, payload
                    FROM public.hasn_sync_events
                    WHERE owner_id = :o AND event_type = 'memory.peer_portrait.upserted'
                    ORDER BY revision DESC
                    LIMIT 1
                    """
                ),
                {'o': owner},
            )
        ).mappings().one()
        assert row['event_type'] == 'memory.peer_portrait.upserted'
        payload = row['payload'] if isinstance(row['payload'], dict) else json.loads(row['payload'])

        # 2) payload 字段名严格对齐 daemon parse_peer_portrait_payload 契约
        assert payload['owner_id'] == owner
        assert payload['peer_hasn_id'] == peer
        assert payload['sync_scope_kind'] == 'owner'
        assert payload['sync_scope_id'] == owner
        assert payload['namespace'] == 'portraits'
        assert payload['record_id'] == peer
        assert payload['peer_kind'] == 'human'
        # 正文键必须是 portrait（非 portrait_text），且非空（合成产物）
        assert isinstance(payload['portrait'], str) and payload['portrait'].strip()
        assert 'portrait_text' not in payload
        # 时间戳 epoch ms int
        assert isinstance(payload['created_at'], int) and payload['created_at'] > 0
        assert isinstance(payload['updated_at'], int) and payload['updated_at'] > 0
        # namespace_revision 单调权威 > 0
        revision = int(payload['namespace_revision'])
        assert revision > 0

        # 3) 可被 pull_memory_events 增量拉取（daemon 端拉取路径）：游标从 0 拉能拿到本事件
        events = await hasn_sync_service.gateway.pull_memory_events(
            session,
            owner_id=owner,
            selections=[
                MemorySyncCursor(
                    sync_scope_kind='owner',
                    sync_scope_id=owner,
                    namespace='portraits',
                    last_pulled_revision=0,
                )
            ],
            limit=50,
        )
        assert any(e.event_type == 'memory.peer_portrait.upserted' for e in events)
        pulled = next(e for e in events if e.event_type == 'memory.peer_portrait.upserted')
        assert pulled.payload['peer_hasn_id'] == peer
        assert pulled.payload['portrait'].strip()

        # 4) 游标推进后（>= 本 revision）不再拉到（已应用不重复下发）
        empty = await hasn_sync_service.gateway.pull_memory_events(
            session,
            owner_id=owner,
            selections=[
                MemorySyncCursor(
                    sync_scope_kind='owner',
                    sync_scope_id=owner,
                    namespace='portraits',
                    last_pulled_revision=revision,
                )
            ],
            limit=50,
        )
        assert all(e.event_type != 'memory.peer_portrait.upserted' for e in empty)
    finally:
        await _cleanup(session, owner)
