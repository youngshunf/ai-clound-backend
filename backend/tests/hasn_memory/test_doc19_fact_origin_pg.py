"""doc19 S3 溯源列与合并裁决 overlay 真实 PG 验收（零 mock）。

设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md
  覆盖事实溯源、状态可见性、删除墓碑、主脑合并和版本汇总

钉死的核心不变量（§3.4）：**裁决过期即自动失效**。事实被合并标成 disputed/merged_into 后，
只要它的业务字段组又被本地整理或主人修改（`revision` 前进），旧裁决立刻作废、事实重新可见，
留待下轮合并重裁——「合并 vs 本地整理」的竞态靠这条判据消解，不靠锁。若这条测试变红，说明
并发护栏没了：被错误裁决的事实会永久消失在注入与检索之外。

需本地 PostgreSQL :15432（不可达则跳过）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.model.fact_tombstone import FactTombstone
from backend.app.hasn_memory.model.merge_request import MergeRequest
from backend.app.hasn_memory.model.merge_run import MergeRun
from backend.app.hasn_memory.model.semantic_fact import SemanticFact
from backend.app.hasn_memory.service.fact_tombstone_service import fact_tombstone_service
from backend.app.hasn_memory.service.merge_request_service import merge_request_service
from backend.app.hasn_memory.service.merge_run_service import merge_run_service
from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.tests.hasn_memory.fact_uplink_seed import seed_local_fact_uplink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
        # 全局 async_db_session 池绑上一事件循环；每测试后释放，避免下个测试 different loop。
        await async_engine.dispose()


async def _cleanup(session: AsyncSession, owner_id: str) -> None:
    await session.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner_id))
    await session.execute(delete(FactTombstone).where(FactTombstone.owner_id == owner_id))
    await session.execute(delete(MergeRun).where(MergeRun.owner_id == owner_id))
    await session.execute(delete(MergeRequest).where(MergeRequest.owner_id == owner_id))
    await session.commit()


async def _save_agent_fact(
    session: AsyncSession,
    owner_id: str,
    agent_id: str,
    *,
    predicate: str = '偏好',
    obj: str = '喜欢冰美式',
    **kwargs: object,
) -> dict:
    """从本地事实上行入口落一条 agent_self 云端镜像。"""
    return await seed_local_fact_uplink(
        session,
        owner_id=owner_id,
        agent_id=agent_id,
        subject_kind='agent_self',
        predicate=predicate,
        object_value=obj,
        **kwargs,  # type: ignore[arg-type]
    )


async def _visible_fact_ids(session: AsyncSession, owner_id: str, agent_id: str, query: str) -> tuple[set, set, set]:
    """同时取 search / recall / list 三条读路的可见 fact_id 集合（三者必须口径一致）。"""
    searched = await semantic_fact_service.search_facts(session, owner_id=owner_id, query=query)
    recalled = await semantic_fact_service.recall_facts(session, owner_id=owner_id, subject_kind='agent_self')
    listed = await semantic_fact_service.list_facts(session, owner_id=owner_id, agent_id=agent_id)
    return (
        {f['fact_id'] for f in searched},
        {f['fact_id'] for f in recalled},
        {f['fact_id'] for f in listed['items']},
    )


# --------------------------------------------------------------------------------------
# 一、新列默认值（§3.2 / §8.2）
# --------------------------------------------------------------------------------------


async def test_fact_uplink_preserves_node_origin_revision_one(session: AsyncSession) -> None:
    """节点上行的事实保留 node origin、revision=1，overlay 三列全空。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    try:
        out = await _save_agent_fact(session, owner, agent)
        assert out['origin_kind'] == 'node'
        assert out['revision'] == 1
        assert out['merge_verdict'] is None
        assert out['merge_verdict_run'] is None
        assert out['merge_judged_revision'] is None
        assert out['valid_until'] is None
        assert out['origin_node_id'].startswith('node_')
        assert out['origin_agent_id'] == agent

        row = (
            await session.execute(select(SemanticFact).where(SemanticFact.fact_id == out['fact_id']))
        ).scalar_one()
        assert row.origin_kind == 'node'
        assert row.revision == 1
        assert row.merged_from == '[]'
    finally:
        await _cleanup(session, owner)


async def test_save_fact_persists_origin_and_valid_until_and_hint(session: AsyncSession) -> None:
    """溯源三列 / valid_until 落库；supersedes_hint 落**正式列**（S2 收敛，不再塞 source_refs_json）。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    node = f'node_{uuid.uuid4().hex[:10]}'
    try:
        first = await _save_agent_fact(session, owner, agent, predicate='住址', obj='老地址')
        second = await _save_agent_fact(
            session,
            owner,
            agent,
            predicate='住址',
            obj='新地址',
            origin_node_id=node,
            origin_agent_id=agent,
            valid_until=4102444800000,
            supersedes_hint=first['fact_id'],
        )
        assert second['origin_node_id'] == node
        assert second['origin_agent_id'] == agent
        assert second['valid_until'] == 4102444800000
        assert second['supersedes_hint'] == first['fact_id']

        row = (
            await session.execute(select(SemanticFact).where(SemanticFact.fact_id == second['fact_id']))
        ).scalar_one()
        # S2 收敛（doc19 §8.2 增列汇总）：hint 落正式列，不再塞 source_refs_json
        assert row.supersedes_hint == first['fact_id']
        assert 'supersedes_hint' not in row.source_refs_json
        # 本片不做裁决：旧事实必须仍然 active、未被隐式取代
        old = (await session.execute(select(SemanticFact).where(SemanticFact.fact_id == first['fact_id']))).scalar_one()
        assert old.status == 'active'
        assert old.superseded_by is None
        assert old.merge_verdict is None
    finally:
        await _cleanup(session, owner)


# --------------------------------------------------------------------------------------
# 二、生效可见性：裁决过期即自动失效（§3.4 核心不变量）
# --------------------------------------------------------------------------------------


async def test_disputed_verdict_hides_fact_then_revision_bump_revives_it(session: AsyncSession) -> None:
    """disputed 且裁决依据 = 当前 revision → 三条读路都查不到；revision 前进 → 重新可见。

    后半段是 §3.4 的并发护栏：事实被裁决后又被本地整理 / 主人确认（revision 前进），旧裁决
    自动作废、事实**立即**恢复可见，无需等下轮合并。
    """
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    marker = f'口味{uuid.uuid4().hex[:6]}'
    try:
        saved = await _save_agent_fact(session, owner, agent, predicate='偏好', obj=marker)
        fact_id = saved['fact_id']

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id in searched
        assert fact_id in recalled
        assert fact_id in listed

        # 合并把它标成 disputed，裁决依据 = 当前 revision（裁决有效）
        await session.execute(
            sa.update(SemanticFact)
            .where(SemanticFact.fact_id == fact_id)
            .values(
                merge_verdict='disputed',
                merge_verdict_run=f'run_{uuid.uuid4().hex[:8]}',
                merge_judged_revision=SemanticFact.revision,
            )
        )
        await session.commit()
        session.expire_all()

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id not in searched
        assert fact_id not in recalled
        assert fact_id not in listed

        # 本地整理 / 主人写使 revision 前进 → 旧裁决过期作废 → 重新可见
        await session.execute(
            sa.update(SemanticFact)
            .where(SemanticFact.fact_id == fact_id)
            .values(revision=SemanticFact.revision + 1)
        )
        await session.commit()
        session.expire_all()

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id in searched, 'revision 前进后 disputed 必须自动失效（§3.4 并发护栏）'
        assert fact_id in recalled
        assert fact_id in listed

        row = (await session.execute(select(SemanticFact).where(SemanticFact.fact_id == fact_id))).scalar_one()
        # 裁决 overlay 原样保留（下轮合并整体替换），只是不再生效——不是被清空
        assert row.merge_verdict == 'disputed'
        assert row.merge_judged_revision == 1
        assert row.revision == 2
    finally:
        await _cleanup(session, owner)


async def test_stale_merged_into_verdict_keeps_fact_visible(session: AsyncSession) -> None:
    """merge_verdict='merged_into' 但 merge_judged_revision 是旧值 → 事实仍然可见。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    marker = f'旧账{uuid.uuid4().hex[:6]}'
    try:
        saved = await _save_agent_fact(session, owner, agent, predicate='结论', obj=marker)
        fact_id = saved['fact_id']
        # 裁决依据 revision=0（比当前 1 旧）：裁决已过期
        await session.execute(
            sa.update(SemanticFact)
            .where(SemanticFact.fact_id == fact_id)
            .values(merge_verdict='merged_into', merge_verdict_run='run_stale', merge_judged_revision=0)
        )
        await session.commit()
        session.expire_all()

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id in searched
        assert fact_id in recalled
        assert fact_id in listed
    finally:
        await _cleanup(session, owner)


async def test_fresh_merged_into_verdict_hides_fact(session: AsyncSession) -> None:
    """merge_verdict='merged_into' 且裁决依据 = 当前 revision → 不喂注入与检索（§5.4 第 6 条同理）。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    marker = f'重复{uuid.uuid4().hex[:6]}'
    try:
        saved = await _save_agent_fact(session, owner, agent, predicate='结论', obj=marker)
        fact_id = saved['fact_id']
        await session.execute(
            sa.update(SemanticFact)
            .where(SemanticFact.fact_id == fact_id)
            .values(
                merge_verdict='merged_into',
                merge_verdict_run='run_fresh',
                merge_judged_revision=SemanticFact.revision,
            )
        )
        await session.commit()
        session.expire_all()

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id not in searched
        assert fact_id not in recalled
        assert fact_id not in listed
    finally:
        await _cleanup(session, owner)


async def test_withdrawn_status_still_hidden_regardless_of_overlay(session: AsyncSession) -> None:
    """业务 status 与 overlay 是两层：status 非 active 时，overlay 为空也照样不可见。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    marker = f'撤回{uuid.uuid4().hex[:6]}'
    try:
        saved = await _save_agent_fact(session, owner, agent, predicate='偏好', obj=marker)
        fact_id = saved['fact_id']
        await session.execute(
            sa.update(SemanticFact).where(SemanticFact.fact_id == fact_id).values(status='withdrawn')
        )
        await session.commit()
        session.expire_all()

        searched, recalled, listed = await _visible_fact_ids(session, owner, agent, marker)
        assert fact_id not in searched
        assert fact_id not in recalled
        assert fact_id not in listed
    finally:
        await _cleanup(session, owner)


# --------------------------------------------------------------------------------------
# 三、三张新表的基本往返
# --------------------------------------------------------------------------------------


async def test_fact_tombstone_roundtrip_and_idempotent(session: AsyncSession) -> None:
    """墓碑登记幂等 + is_purged / filter_purged 判据可用（§4.5 防复活）。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    fact_id = uuid.uuid4().hex
    derived_id = uuid.uuid4().hex
    try:
        assert await fact_tombstone_service.is_purged(session, fact_id) is False

        first = await fact_tombstone_service.record_purge(
            session, fact_id=fact_id, owner_id=owner, purged_by=owner, reason='主人在记忆页彻底删除'
        )
        await session.commit()
        assert first.fact_id == fact_id
        assert first.cascade_from is None
        assert first.purged_time is not None

        # 幂等：purge 广播重复到达不改写首次删除事实
        again = await fact_tombstone_service.record_purge(
            session, fact_id=fact_id, owner_id=owner, purged_by='h_someone_else', reason='重复广播'
        )
        await session.commit()
        assert again.purged_by == owner
        assert again.reason == '主人在记忆页彻底删除'

        # 级联删除的派生事实指向源 fact_id
        await fact_tombstone_service.record_purge(
            session, fact_id=derived_id, owner_id=owner, purged_by=owner, cascade_from=fact_id
        )
        await session.commit()

        assert await fact_tombstone_service.is_purged(session, fact_id) is True
        assert await fact_tombstone_service.filter_purged(session, [fact_id, derived_id, 'never_existed']) == {
            fact_id,
            derived_id,
        }
        rows = await fact_tombstone_service.list_by_owner(session, owner)
        assert {r.fact_id for r in rows} == {fact_id, derived_id}
        assert await fact_tombstone_service.filter_purged(session, []) == set()
    finally:
        await _cleanup(session, owner)


async def test_merge_run_roundtrip_applied_and_rejected(session: AsyncSession) -> None:
    """合并轮次往返：applied 可被 latest_applied 取到；rejected 也留痕且必须带 reject_reason。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    node = f'node_{uuid.uuid4().hex[:10]}'
    agent = f'a_d19_{uuid.uuid4().hex[:8]}'
    applied_id = uuid.uuid4().hex
    rejected_id = uuid.uuid4().hex
    try:
        applied = await merge_run_service.record_run(
            session,
            run_id=applied_id,
            owner_id=owner,
            submitted_node_id=node,
            submitted_agent_id=agent,
            base_owner_memory_version=3,
            status='applied',
            facts_judged=12,
            facts_merged=2,
            facts_disputed=1,
            summary='把两条重复的住址合成一条，撤了一条过期的联系方式',
        )
        await session.commit()
        assert applied.run_id == applied_id
        assert applied.finished_time is not None

        rejected = await merge_run_service.record_run(
            session,
            run_id=rejected_id,
            owner_id=owner,
            submitted_node_id=node,
            submitted_agent_id=agent,
            base_owner_memory_version=3,
            status='rejected',
            reject_reason='version_conflict',
        )
        await session.commit()
        assert rejected.status == 'rejected'

        # rejected 必须给原因——否则主脑下轮不知道为什么被拒（§5.6）
        with pytest.raises(ValueError, match='reject_reason'):
            await merge_run_service.record_run(
                session,
                run_id=uuid.uuid4().hex,
                owner_id=owner,
                submitted_node_id=node,
                submitted_agent_id=agent,
                base_owner_memory_version=3,
                status='rejected',
            )
        await session.rollback()

        with pytest.raises(ValueError, match='非法轮次结果'):
            await merge_run_service.record_run(
                session,
                run_id=uuid.uuid4().hex,
                owner_id=owner,
                submitted_node_id=node,
                submitted_agent_id=agent,
                base_owner_memory_version=3,
                status='pending',
            )
        await session.rollback()

        latest = await merge_run_service.latest_applied(session, owner)
        assert latest is not None
        assert latest.run_id == applied_id
        assert latest.submitted_node_id == node
        assert latest.facts_disputed == 1
        assert len(await merge_run_service.list_by_owner(session, owner)) == 2
    finally:
        await _cleanup(session, owner)


async def test_merge_request_dedupes_to_latest_and_consumes_once(session: AsyncSession) -> None:
    """合并待办每 owner 至多一条（重复请求覆盖成最新），消化一次后不再是 pending。"""
    owner = f'h_d19_{uuid.uuid4().hex[:8]}'
    agent_a = f'a_d19_{uuid.uuid4().hex[:8]}'
    agent_b = f'a_d19_{uuid.uuid4().hex[:8]}'
    node_a = f'node_{uuid.uuid4().hex[:10]}'
    node_b = f'node_{uuid.uuid4().hex[:10]}'
    try:
        await merge_request_service.request_merge(
            session, owner_id=owner, requested_by_agent=agent_a, requested_by_node=node_a, reason='local_review_done'
        )
        await session.commit()
        await merge_request_service.request_merge(
            session, owner_id=owner, requested_by_agent=agent_b, requested_by_node=node_b, reason='owner_manual'
        )
        await session.commit()

        # 主键去重：只剩最新一条，不堆积
        total = (
            await session.execute(
                sa.select(sa.func.count()).select_from(MergeRequest).where(MergeRequest.owner_id == owner)
            )
        ).scalar_one()
        assert total == 1

        pending = await merge_request_service.get_pending(session, owner)
        assert pending is not None
        assert pending.requested_by_agent == agent_b
        assert pending.requested_by_node == node_b
        assert pending.reason == 'owner_manual'
        assert pending.consumed_time is None

        assert await merge_request_service.consume(session, owner) is True
        await session.commit()
        assert await merge_request_service.get_pending(session, owner) is None
        # 重复消化不假装又吃掉了一条
        assert await merge_request_service.consume(session, owner) is False

        # 消化后再请求：同一行被重新置为未消化
        await merge_request_service.request_merge(
            session, owner_id=owner, requested_by_agent=agent_a, requested_by_node=node_a, reason='local_review_done'
        )
        await session.commit()
        revived = await merge_request_service.get_pending(session, owner)
        assert revived is not None
        assert revived.requested_by_agent == agent_a
    finally:
        await _cleanup(session, owner)
