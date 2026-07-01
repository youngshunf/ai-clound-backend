"""Peer 画像端到端全链路真实 PG 验收（doc17 PEERSYN-P6 §12，零 mock）。

在 P2（合成/演化）、P3（发射契约/可拉取）之上，补齐 §12 四闭环的**云端全链路串联** +
**隐私隔离**，走**生产 sweep 路径**（`sweep_peer_portraits`，而非直调 `synthesize_peer_portrait`）：

1. **多 Agent 合并（§12.1）**：同 owner 名下**两个分身**各自对同一对方 P 积累 peer 事实
   （`subject_kind='peer', subject_id=P, agent_id=NULL`、owner-scoped）→ 跑 sweep → 断言
   **仅一份** `peer_portrait(owner,P)`、`source_fact_count` 涵盖全部、正文涵盖两路观察。
2. **发射 + 可拉取（§12.3）**：sweep 合成后往 `hasn_sync_events` 落一条
   `memory.peer_portrait.upserted`，`pull_memory_events`（namespace 游标增量）能拉到。
3. **隐私隔离（§12.6）**：**另一个 owner B** 用同 namespace 游标拉取，**拉不到** owner A 的
   画像事件（下行事件 owner-scoped，绝不串到别的 owner——peer 画像是主人私有认知）。

LLM 用 service 既有注入式 ``llm_complete`` 打桩（sanctioned 注入点，非业务 mock）。需本地
PostgreSQL :15432（否则 skip）。真跑：`DATABASE_PORT=15432 uv run pytest
backend/tests/hasn_memory/test_peer_portrait_e2e_pg.py`。
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
        # 回显 user 内容（含被聚合的全部 peer 事实）→ 证明多分身事实合成进同一份画像。
        user = next((m['content'] for m in messages if m['role'] == 'user'), '')
        return '画像｜' + user.replace('\n', ' ')

    return _complete


async def _seed_peer_fact(
    session: AsyncSession, owner_id: str, peer_hasn_id: str, *, predicate: str, obj: str
) -> None:
    # peer 事实 agent_id 天然 NULL、owner-scoped——模拟「某分身对该对方的一条观察」。
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


async def _cleanup(session: AsyncSession, *owner_ids: str) -> None:
    # 测试脚手架 owner（随机 uuid），清干净：画像/事实/下行事件/namespace 游标。
    for owner_id in owner_ids:
        await session.execute(delete(PeerPortrait).where(PeerPortrait.owner_id == owner_id))
        await session.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner_id))
        await session.execute(
            sa.text('DELETE FROM public.hasn_sync_events WHERE owner_id = :o'), {'o': owner_id}
        )
        await session.execute(
            sa.text('DELETE FROM hasn_memory.namespace_revision WHERE sync_scope_id = :o'),
            {'o': owner_id},
        )
    await session.commit()


async def test_peer_portrait_full_chain_multiagent_merge_emit_pull_and_privacy(
    session: AsyncSession,
) -> None:
    """多分身聚合 → sweep 合成一份 → 发射可拉取 → 跨 owner 拉不到（隐私隔离）。"""
    owner_a = f'h_ppe_a_{uuid.uuid4().hex[:8]}'
    owner_b = f'h_ppe_b_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'

    # 两个分身各自的一路观察（均 owner_a 名下、owner-scoped、agent_id NULL）。
    await _seed_peer_fact(session, owner_a, peer, predicate='职业', obj='投资人')
    await _seed_peer_fact(session, owner_a, peer, predicate='沟通偏好', obj='喜欢简洁直接不要寒暄')

    try:
        # ── §12.1 多 Agent 合并：走生产 sweep 路径（非直调 synthesize）。
        summary = await peer_portrait_service.sweep_peer_portraits(
            owner_ids=[owner_a], llm_complete=_echo_llm()
        )
        assert summary['synthesized'] >= 1, summary

        rows = (
            await session.execute(
                select(PeerPortrait).where(
                    PeerPortrait.owner_id == owner_a, PeerPortrait.peer_hasn_id == peer
                )
            )
        ).scalars().all()
        assert len(rows) == 1, '多分身对同一对方只应合成「一份」权威画像'
        portrait_row = rows[0]
        # 两路观察都进合成输入（echo 回显）→ 证明 recall 聚合成一份、涵盖全部来源。
        assert portrait_row.source_fact_count >= 2, portrait_row.source_fact_count
        assert '投资人' in portrait_row.portrait_text
        assert '喜欢简洁直接不要寒暄' in portrait_row.portrait_text
        assert portrait_row.peer_kind == 'human'
        assert portrait_row.version >= 1

        # ── §12.3 发射 + 可拉取：owner_a 的 portraits 游标能拉到该下行事件。
        events = await hasn_sync_service.gateway.pull_memory_events(
            session,
            owner_id=owner_a,
            selections=[
                MemorySyncCursor(
                    sync_scope_kind='owner',
                    sync_scope_id=owner_a,
                    namespace='portraits',
                    last_pulled_revision=0,
                )
            ],
            limit=50,
        )
        pulled = [e for e in events if e.event_type == 'memory.peer_portrait.upserted']
        assert pulled, '合成后必须能拉到 memory.peer_portrait.upserted 下行事件'
        payload = pulled[0].payload
        assert payload['peer_hasn_id'] == peer
        assert payload['owner_id'] == owner_a
        assert payload['sync_scope_id'] == owner_a
        assert payload['namespace'] == 'portraits'
        # 正文键是 portrait（daemon 契约）、非空、涵盖两路观察。
        assert 'portrait_text' not in payload
        assert '投资人' in payload['portrait'] and '喜欢简洁直接不要寒暄' in payload['portrait']
        # 直查 hasn_sync_events 双保险（payload 形态与 pull 一致）。
        raw = (
            await session.execute(
                sa.text(
                    """
                    SELECT payload FROM public.hasn_sync_events
                    WHERE owner_id = :o AND event_type = 'memory.peer_portrait.upserted'
                    ORDER BY revision DESC LIMIT 1
                    """
                ),
                {'o': owner_a},
            )
        ).scalar_one()
        raw_payload = raw if isinstance(raw, dict) else json.loads(raw)
        assert raw_payload['peer_hasn_id'] == peer

        # ── §12.6 隐私隔离：owner_b 用同 namespace 游标拉取，绝不应看到 owner_a 的画像事件。
        cross = await hasn_sync_service.gateway.pull_memory_events(
            session,
            owner_id=owner_b,
            selections=[
                MemorySyncCursor(
                    sync_scope_kind='owner',
                    sync_scope_id=owner_b,
                    namespace='portraits',
                    last_pulled_revision=0,
                )
            ],
            limit=50,
        )
        assert all(
            e.event_type != 'memory.peer_portrait.upserted' for e in cross
        ), 'peer 画像是主人私有认知，绝不下发给其他 owner'
    finally:
        await _cleanup(session, owner_a, owner_b)
