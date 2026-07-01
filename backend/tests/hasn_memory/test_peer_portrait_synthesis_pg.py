"""Peer 画像合成服务真实 PG 验收（doc17 PEERSYN-P2，零 mock）。

验证 doc17 核心决策：
- **多 Agent 合并 = 事实聚合**：同 owner 名下多个分身对同一对方积累的 peer 事实（agent_id
  IS NULL、owner-scoped）被 `recall_facts(subject_kind='peer')` 一次全取回合成成一份画像，
  source_fact_count 涵盖全部（§5.1）；
- **基线演化**：第二次合成把现有画像作基线喂 LLM、version++（不推倒重来）；
- **同主人分身跳过**：对方是本 owner 名下自己的分身 → 不合成（runtime 侧本就不注入，§5.6）；
- **外部分身照常**：对方是别 owner 的分身（a_ 前缀但非本 owner 名下）→ peer_kind='agent' 照常合成；
- **无事实不写空画像**；**LLM 产空抛错、不写假画像**；
- **方案B 脏扫描**：sweep 只挑「事实晚于上次合成」的对。

LLM 用 service 既有注入式 ``llm_complete`` 打桩（与 owner_memory 同一 sanctioned 注入点，
非业务 mock——不伪造画像内容，只替换出网关那一跳让用例确定）。需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
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
        # sweep 内部用全局 async_db_session；pytest-asyncio 每测试新事件循环，全局池绑上一循环 →
        # 下个测试报 different loop。每测试后释放全局池。
        await async_engine.dispose()


async def _seed_peer_fact(session: AsyncSession, owner_id: str, peer_hasn_id: str, *, predicate: str, obj: str) -> None:
    """落一条 peer 语义事实（agent_id 天然 NULL、owner-scoped）——模拟某分身对该对方的观察。"""
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
    await session.execute(delete(PeerPortrait).where(PeerPortrait.owner_id == owner_id))
    await session.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner_id))
    await session.execute(delete(HasnAgents).where(HasnAgents.owner_id == owner_id))
    await session.commit()


def _echo_llm(marker: str = '') -> Callable[[list[dict[str, str]]], Awaitable[str]]:
    """打桩 LLM：把喂进来的事实/基线回显成画像正文（便于断言纳入了哪些事实/基线）。"""

    async def _complete(messages: list[dict[str, str]]) -> str:  # noqa: RUF029 — async 匹配 Awaitable 协议
        user = next((m['content'] for m in messages if m['role'] == 'user'), '')
        return f'画像{marker}｜' + user.replace('\n', ' ')

    return _complete


async def test_synthesize_aggregates_facts_across_agents(session: AsyncSession) -> None:
    """跨分身的多条 peer 事实被聚合进一份画像：version=1、source_fact_count=全部、peer_kind=human。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    # 模拟两个分身各自对同一对方的观察（peer 事实 agent_id 均 NULL、owner-scoped）
    await _seed_peer_fact(session, owner, peer, predicate='职业', obj='投资人')
    await _seed_peer_fact(session, owner, peer, predicate='沟通偏好', obj='喜欢简洁直接')
    await _seed_peer_fact(session, owner, peer, predicate='关系', obj='主人的重要客户')

    try:
        out = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=peer, llm_complete=_echo_llm()
        )
        await session.commit()
        assert out['synthesized'] is True
        assert out['version'] == 1
        assert out['peer_kind'] == 'human'
        assert out['source_fact_count'] == 3

        row = (
            await session.execute(
                select(PeerPortrait).where(PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == peer)
            )
        ).scalar_one()
        # 三条事实（跨分身）都进了合成输入（echo 回显）→ 证明 recall 聚合成一份
        assert '投资人' in row.portrait_text
        assert '喜欢简洁直接' in row.portrait_text
        assert '重要客户' in row.portrait_text
        assert row.source_fact_count == 3
        assert row.last_synthesized_at and row.last_synthesized_at > 0
        assert row.last_interaction_at and row.last_interaction_at > 0
    finally:
        await _cleanup(session, owner)


async def test_second_synthesis_bumps_version_with_baseline(session: AsyncSession) -> None:
    """第二次合成：现有画像作基线喂 LLM + version++（基线演化，不推倒重来）。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    await _seed_peer_fact(session, owner, peer, predicate='职业', obj='设计师')

    try:
        first = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=peer, llm_complete=_echo_llm('V1')
        )
        await session.commit()
        assert first['version'] == 1

        # 新增一条观察后再合成：断言 LLM 收到的 messages 里带上了 V1 画像作基线
        await _seed_peer_fact(session, owner, peer, predicate='近况', obj='在筹备新公司')
        captured: dict[str, str] = {}

        async def _capture(messages: list[dict[str, str]]) -> str:  # noqa: RUF029
            captured['user'] = next((m['content'] for m in messages if m['role'] == 'user'), '')
            return '画像V2｜融合了新公司近况'

        second = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=peer, llm_complete=_capture
        )
        await session.commit()
        assert second['version'] == 2
        # 基线演化：第二次合成的用户提示词里包含第一次的画像正文（现有画像作基线）
        assert '画像V1' in captured['user']
        assert '在筹备新公司' in captured['user']  # 新事实也进来了

        row = (
            await session.execute(
                select(PeerPortrait).where(PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == peer)
            )
        ).scalar_one()
        assert row.version == 2
        assert '新公司近况' in row.portrait_text
        assert row.source_fact_count == 2
    finally:
        await _cleanup(session, owner)


async def test_skips_same_owner_agent(session: AsyncSession) -> None:
    """对方是本 owner 名下自己的分身 → 不合成（runtime 侧本就不注入 peer 画像，§5.6）。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    my_agent = f'a_mine_{uuid.uuid4().hex[:8]}'
    # 建一条本 owner 名下的分身
    session.add(
        HasnAgents(
            hasn_id=my_agent,
            star_id=f'{uuid.uuid4().hex[:6]}#mine',
            owner_id=owner,
            display_name='我的分身',
            agent_name='mine',
            type='desktop',
            role='specialist',
            api_key_hash='hash',
            status='active',
            created_via='client',
        )
    )
    await session.commit()
    # 即使存在对该分身的 peer 事实，也应跳过合成
    await _seed_peer_fact(session, owner, my_agent, predicate='测试', obj='不该被合成')

    try:
        out = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=my_agent, llm_complete=_echo_llm()
        )
        assert out['synthesized'] is False
        assert out['reason'] == 'same_owner_agent'
        assert (
            await session.execute(
                select(PeerPortrait).where(PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == my_agent)
            )
        ).scalar_one_or_none() is None
    finally:
        await _cleanup(session, owner)


async def test_external_agent_peer_synthesized(session: AsyncSession) -> None:
    """对方是别 owner 的分身（a_ 前缀但非本 owner 名下）→ peer_kind='agent' 照常合成。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    external_agent = f'a_ext_{uuid.uuid4().hex[:8]}'  # 不在本 owner 的 hasn_agents 里
    await _seed_peer_fact(session, owner, external_agent, predicate='服务领域', obj='法律咨询分身')

    try:
        out = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=external_agent, llm_complete=_echo_llm()
        )
        await session.commit()
        assert out['synthesized'] is True
        assert out['peer_kind'] == 'agent'  # a_ 前缀 + 非本 owner 名下 → 外部分身
        row = (
            await session.execute(
                select(PeerPortrait).where(
                    PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == external_agent
                )
            )
        ).scalar_one()
        assert row.peer_kind == 'agent'
    finally:
        await _cleanup(session, owner)


async def test_no_facts_skips(session: AsyncSession) -> None:
    """无 active peer 事实 → 不写空画像。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    try:
        out = await peer_portrait_service.synthesize_peer_portrait(
            session, owner_id=owner, peer_hasn_id=peer, llm_complete=_echo_llm()
        )
        assert out['synthesized'] is False
        assert out['reason'] == 'no_facts'
    finally:
        await _cleanup(session, owner)


async def test_empty_llm_output_raises_no_fake(session: AsyncSession) -> None:
    """LLM 产空 → 抛错、不写假画像（零 fake）。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    await _seed_peer_fact(session, owner, peer, predicate='职业', obj='医生')

    async def _empty(_messages: list[dict[str, str]]) -> str:  # noqa: RUF029
        return '   '

    try:
        with pytest.raises(ValueError, match='empty'):
            await peer_portrait_service.synthesize_peer_portrait(
                session, owner_id=owner, peer_hasn_id=peer, llm_complete=_empty
            )
        await session.rollback()
        assert (
            await session.execute(
                select(PeerPortrait).where(PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == peer)
            )
        ).scalar_one_or_none() is None
    finally:
        await _cleanup(session, owner)


async def test_sweep_dirty_detection(session: AsyncSession) -> None:
    """方案B 脏扫描：有新事实但画像未追上的对被合成；追上后不再重复合成。"""
    owner = f'h_pp_{uuid.uuid4().hex[:8]}'
    peer = f'h_peer_{uuid.uuid4().hex[:8]}'
    await _seed_peer_fact(session, owner, peer, predicate='职业', obj='工程师')

    try:
        # 第一轮 sweep（owner_ids 隔离，不波及别 owner）：脏对被合成
        s1 = await peer_portrait_service.sweep_peer_portraits(owner_ids=[owner], llm_complete=_echo_llm())
        assert s1['synthesized'] >= 1
        row = (
            await session.execute(
                select(PeerPortrait).where(PeerPortrait.owner_id == owner, PeerPortrait.peer_hasn_id == peer)
            )
        ).scalar_one()
        assert row.version == 1

        # 第二轮 sweep：事实未再更新，画像已追上 → 该对不再是脏对（candidates 不含它）
        s2 = await peer_portrait_service.sweep_peer_portraits(owner_ids=[owner], llm_complete=_echo_llm())
        assert s2['candidates'] == 0
        assert s2['synthesized'] == 0
    finally:
        await _cleanup(session, owner)
