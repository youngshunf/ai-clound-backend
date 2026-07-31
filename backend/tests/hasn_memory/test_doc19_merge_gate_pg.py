"""doc19 S6-cloud · 云端合并闸的真实 PG 验收（零 mock）。

设计事实源：``docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md``
  §3.4 两层状态与逐条失效护栏 · §4.4 谁是主脑 · §5.5 主脑单点可见 · §5.6 幂等与并发 ·
  §8.5 云端职责边界 · D-3 / D-4 / D-17 / D-18

逐条覆盖六步校验与两条退役回归：

1. 主脑提交成功 → ``test_master_brain_apply_*``
2. 非主脑提交 409 ``not_master_brain``（D-18 核心）→ ``test_non_master_brain_rejected``
3. 主脑换绑窗口（是主脑但节点不是当前绑定）→ ``test_master_brain_wrong_node_rejected``
4. CAS 过期 → 409 ``version_conflict`` 且**整轮零副作用** → ``test_version_conflict_*``
5. run_id 重复 → 返回上次结果不重复应用 → ``test_duplicate_run_id_replays``
6. 逐条失效护栏 → ``test_stale_verdict_skipped_others_applied``
7. 整轮替换（D-4）→ ``test_second_run_replaces_previous_overlay``
8. 拒绝留痕 → ``test_rejection_records_merge_run``
9. 合并待办去重 → ``test_merge_request_dedup_keeps_latest``
10. contribute 不再内联合并 → ``test_contribute_no_longer_merges_inline``
11. 云端语义处理退役回归 → ``test_cloud_memory_semantic_processing_retired``
12. 退化输入（`owner_memory` 缺键 / 三数组皆空）→ ``test_missing_owner_memory_key_*`` /
    ``test_fully_degenerate_round_applies_cleanly`` / ``test_empty_owner_memory_content_*``
13. 跨仓提交体字段名钉子 → ``test_merge_request_body_field_names_match_local_rust_builder``

需本地 PostgreSQL :15432（不可达则跳过）。
"""

from __future__ import annotations

import re
import uuid

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_memory.model import HasnOwnerMemory, HasnOwnerMemoryContribution, MergeRequest, MergeRun
from backend.app.hasn_memory.model.peer_portrait import PeerPortrait
from backend.app.hasn_memory.schema.merge_gate import (
    MergeApplyRequest,
    MergeApplyResponse,
    MergeDerivedFactItem,
    MergeOwnerMemoryPayload,
    MergePeerPortraitItem,
    MergeRequestBody,
    MergeStats,
    MergeVerdictItem,
)
from backend.app.hasn_memory.service.merge_gate_service import (
    REJECT_NOT_MASTER_BRAIN,
    REJECT_VERSION_CONFLICT,
    MergeGateRejectedError,
    merge_gate_service,
)
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.database.schema_names import SCHEMA_NAMES
from backend.tests.hasn_memory.local_rust_source import find_local_rust_source

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')


# --------------------------------------------------------------------------------------
# 夹具：一个主人 + 两个分身（主脑 role='primary' 在 NODE_A，另一个非主脑在 NODE_B）
# --------------------------------------------------------------------------------------


class Fixture:
    """本轮测试用到的真实身份（全部落真库，不 mock）。"""

    def __init__(self) -> None:
        marker = uuid.uuid4().hex
        self.owner_id = f'h_mg{marker[:20]}'
        self.master_agent_id = f'a_mg1{marker[:19]}'
        self.other_agent_id = f'a_mg2{marker[:19]}'
        self.node_a = f'node_{marker[:12]}'
        self.node_b = f'node_{marker[12:24]}'


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AsyncSession, Fixture]]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fx = Fixture()
    async with sessions.begin() as setup:
        setup.add_all([
            HasnHumans(
                hasn_id=fx.owner_id,
                star_id=f'h{uuid.uuid4().hex[:24]}',
                nickname='合并闸主人',
                status='active',
            ),
            # 主脑：role='primary'，绑定在 NODE_A（无 workbench_pref 时按 role 回落解析）
            HasnAgents(
                hasn_id=fx.master_agent_id,
                star_id=f'a{uuid.uuid4().hex[:24]}',
                owner_id=fx.owner_id,
                display_name='主脑分身',
                agent_name=f'master{uuid.uuid4().hex[:8]}',
                role='primary',
                status='active',
                binding_node_id=fx.node_a,
            ),
            HasnAgents(
                hasn_id=fx.other_agent_id,
                star_id=f'a{uuid.uuid4().hex[:24]}',
                owner_id=fx.owner_id,
                display_name='普通分身',
                agent_name=f'other{uuid.uuid4().hex[:8]}',
                role='specialist',
                status='active',
                binding_node_id=fx.node_b,
            ),
        ])

    session = sessions()
    try:
        yield session, fx
    finally:
        await session.rollback()
        await session.close()
        async with sessions.begin() as cleanup:
            for stmt in (
                sa.text('DELETE FROM hasn_memory.semantic_fact WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.peer_portrait WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.merge_run WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.merge_request WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.owner_memory WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.owner_memory_contribution WHERE owner_id = :o'),
                sa.text(f'DELETE FROM {_SYNC_EVENTS} WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_agents WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_humans WHERE hasn_id = :o'),
            ):
                await cleanup.execute(stmt, {'o': fx.owner_id})
        await engine.dispose()
        # 全局 async_db_session 池绑上一事件循环（拒绝留痕走它）；每测试后释放。
        await async_engine.dispose()


async def _seed_fact(
    db: AsyncSession,
    *,
    owner_id: str,
    node_id: str,
    agent_id: str,
    revision: int = 1,
    predicate: str = '偏好',
) -> str:
    """种一条本节点自产的活跃事实（真实落库，字段与本地 crate 双端一致）。"""
    fact_id = uuid.uuid4().hex
    await db.execute(
        sa.text(
            """
            INSERT INTO hasn_memory.semantic_fact (
                fact_id, owner_id, agent_id, subject_kind, subject_id, memory_layer,
                scope_kind, scope_id, predicate, object_json, confidence, status,
                source_turn_ids, source_refs_json, created_at, updated_at,
                origin_kind, origin_node_id, origin_agent_id, merged_from, revision
            ) VALUES (
                :fact_id, :owner_id, :agent_id, 'agent_self', :agent_id, 'semantic',
                'global', :agent_id, :predicate, '"喜欢冰美式"', 0.8, 'active',
                '[]', '[]', 1, 1,
                'node', :node_id, :agent_id, '[]', :revision
            )
            """
        ),
        {
            'fact_id': fact_id,
            'owner_id': owner_id,
            'agent_id': agent_id,
            'node_id': node_id,
            'predicate': predicate,
            'revision': revision,
        },
    )
    await db.flush()
    return fact_id


async def _overlay(db: AsyncSession, fact_id: str) -> tuple[Any, Any, Any]:
    row = (
        await db.execute(
            sa.text(
                'SELECT merge_verdict, merge_verdict_run, merge_judged_revision '
                'FROM hasn_memory.semantic_fact WHERE fact_id = :f'
            ),
            {'f': fact_id},
        )
    ).first()
    return (row[0], row[1], row[2]) if row is not None else (None, None, None)


def _request(fx: Fixture, **overrides: Any) -> MergeApplyRequest:
    payload: dict[str, Any] = {
        'run_id': f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': fx.node_a,
        'base_owner_memory_version': 0,
        'verdicts': [],
        'derived_facts': [],
        'owner_memory': None,
        'peer_portraits': [],
        'summary': '本轮把两条重复的咖啡偏好合成了一条',
        'stats': MergeStats(facts_judged=2, facts_merged=1, facts_disputed=0),
    }
    payload.update(overrides)
    return MergeApplyRequest(**payload)


# --------------------------------------------------------------------------------------
# 1 · 主脑提交成功（overlay + 派生 + USER.md + 画像 + 轮次留痕 + 待办消化）
# --------------------------------------------------------------------------------------


async def test_master_brain_apply_writes_overlay_derived_memory_and_portrait(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.6 整轮原子应用：五类写入一次到位，且 owner_memory.version 从 0 推到 1。"""
    db, fx = env
    fact_id = await _seed_fact(db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id)
    derived_id = uuid.uuid4().hex
    peer_id = f'h_peer{uuid.uuid4().hex[:16]}'

    body = _request(
        fx,
        verdicts=[MergeVerdictItem(fact_id=fact_id, verdict='merged_into', judged_revision=1)],
        derived_facts=[
            MergeDerivedFactItem(
                fact_id=derived_id,
                predicate='咖啡偏好',
                object_json='"主人上午喝冰美式，下午不喝咖啡"',
                subject_kind='owner',
                subject_id=fx.owner_id,
                scope_kind='global',
                confidence=0.9,
                merged_from=[fact_id],
                rationale='两条同主体同谓词事实语义等价，合成一条',
            )
        ],
        owner_memory=MergeOwnerMemoryPayload(content='健康: 主人注重抗衰老'),
        peer_portraits=[MergePeerPortraitItem(peer_hasn_id=peer_id, portrait_text='沟通直接，偏好要点式汇报')],
    )
    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)

    assert result.applied is True
    assert result.replayed is False
    assert result.new_owner_memory_version == 1
    assert result.skipped_verdicts == []
    assert result.derived_created == 1
    assert result.portraits_updated == 1

    verdict, run, judged = await _overlay(db, fact_id)
    assert (verdict, run, judged) == ('merged_into', body.run_id, 1)

    derived = (
        await db.execute(
            sa.text(
                'SELECT origin_kind, origin_node_id, origin_agent_id, merged_from, status, revision '
                'FROM hasn_memory.semantic_fact WHERE fact_id = :f'
            ),
            {'f': derived_id},
        )
    ).first()
    # §3.2：派生事实不属于任何节点自产片，溯源两列必须为空
    assert derived is not None
    assert derived[0] == 'merged'
    assert derived[1] is None
    assert derived[2] is None
    assert fact_id in derived[3]
    assert derived[4] == 'active'
    assert derived[5] == 1

    memory = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert memory.version == 1
    assert memory.last_merge_run_id == body.run_id
    assert memory.last_merge_node_id == fx.node_a
    assert memory.last_merge_summary == body.summary
    # 身份兜底（_ensure_identity_lines）：昵称与 Owner HASN ID 必须被补回，绝不因合并丢失
    assert '称呼: 合并闸主人' in memory.content
    assert fx.owner_id in memory.content

    portrait = (
        await db.execute(select(PeerPortrait).where(PeerPortrait.owner_id == fx.owner_id))
    ).scalar_one()
    assert portrait.peer_hasn_id == peer_id
    assert portrait.version == 1
    assert portrait.revised_by == fx.master_agent_id

    run_row = (await db.execute(select(MergeRun).where(MergeRun.run_id == body.run_id))).scalar_one()
    assert run_row.status == 'applied'
    assert run_row.reject_reason is None
    assert run_row.submitted_agent_id == fx.master_agent_id
    assert run_row.facts_judged == 2


async def test_master_brain_apply_bumps_agent_user_md_for_mempush(env: tuple[AsyncSession, Fixture]) -> None:
    """MEMPUSH（doc19 §10 保留）：合并后覆盖该 owner 全部分身 user_md 并 bump profile_revision。"""
    db, fx = env
    before = dict(
        (
            await db.execute(
                sa.select(HasnAgents.hasn_id, HasnAgents.profile_revision).where(HasnAgents.owner_id == fx.owner_id)
            )
        ).all()
    )
    body = _request(fx, owner_memory=MergeOwnerMemoryPayload(content='工作: 主人主攻 Rust'))
    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)

    after = (
        await db.execute(
            sa.select(HasnAgents.hasn_id, HasnAgents.user_md, HasnAgents.profile_revision).where(
                HasnAgents.owner_id == fx.owner_id
            )
        )
    ).all()
    assert len(after) == 2
    for hasn_id, user_md, revision in after:
        assert '主攻 Rust' in user_md
        assert revision == before[hasn_id] + 1


# --------------------------------------------------------------------------------------
# 2 · 非主脑提交被拒（D-18 核心）
# --------------------------------------------------------------------------------------


async def test_non_master_brain_rejected(env: tuple[AsyncSession, Fixture]) -> None:
    """D-18：非主脑分身提交整轮被拒——本地互斥防不住主脑换绑窗口，权威互斥在云端。"""
    db, fx = env
    body = _request(fx, node_id=fx.node_b)
    with pytest.raises(MergeGateRejectedError) as excinfo:
        await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.other_agent_id, body=body)
    assert excinfo.value.reason == REJECT_NOT_MASTER_BRAIN


async def test_master_brain_wrong_node_rejected(env: tuple[AsyncSession, Fixture]) -> None:
    """§4.4 换绑窗口：分身还是主脑，但提交来自它已经离开（或从未绑定）的那台设备 → 拒。"""
    db, fx = env
    body = _request(fx, node_id=fx.node_b)  # 主脑绑在 node_a
    with pytest.raises(MergeGateRejectedError) as excinfo:
        await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    assert excinfo.value.reason == REJECT_NOT_MASTER_BRAIN
    assert excinfo.value.detail == 'node_mismatch'


# --------------------------------------------------------------------------------------
# 3 · CAS 与整轮零副作用
# --------------------------------------------------------------------------------------


async def test_version_conflict_rejected_with_zero_side_effect(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.6 CAS：基线过期整轮拒绝，且 overlay 没写、派生没建、version 没动（零副作用）。"""
    db, fx = env
    fact_id = await _seed_fact(db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id)
    derived_id = uuid.uuid4().hex
    # 先成功跑一轮，把 version 推到 1
    first = _request(fx, owner_memory=MergeOwnerMemoryPayload(content='基线一轮'))
    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=first)
    await db.commit()

    stale = _request(
        fx,
        base_owner_memory_version=0,  # 已经过期（库中是 1）
        verdicts=[MergeVerdictItem(fact_id=fact_id, verdict='disputed', judged_revision=1)],
        derived_facts=[
            MergeDerivedFactItem(
                fact_id=derived_id,
                predicate='不该出现的派生事实',
                object_json='"x"',
                subject_kind='owner',
                subject_id=fx.owner_id,
            )
        ],
        owner_memory=MergeOwnerMemoryPayload(content='不该落库的正文'),
    )
    with pytest.raises(MergeGateRejectedError) as excinfo:
        await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=stale)
    assert excinfo.value.reason == REJECT_VERSION_CONFLICT
    await db.rollback()

    verdict, _run, _judged = await _overlay(db, fact_id)
    assert verdict is None  # overlay 没写
    exists = (
        await db.execute(
            sa.text('SELECT 1 FROM hasn_memory.semantic_fact WHERE fact_id = :f'), {'f': derived_id}
        )
    ).first()
    assert exists is None  # 派生没建
    memory = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert memory.version == 1  # version 没动
    assert '不该落库' not in (memory.content or '')


async def test_rejection_records_merge_run(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.5 拒绝必须留痕：请求事务回滚了，`merge_run(status='rejected')` 仍在（独立事务）。"""
    db, fx = env
    body = _request(fx, node_id=fx.node_b)
    with pytest.raises(MergeGateRejectedError):
        await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    await db.rollback()

    run_row = (await db.execute(select(MergeRun).where(MergeRun.run_id == body.run_id))).scalar_one()
    assert run_row.status == 'rejected'
    assert run_row.reject_reason == REJECT_NOT_MASTER_BRAIN
    assert run_row.owner_id == fx.owner_id
    assert run_row.finished_time is not None


# --------------------------------------------------------------------------------------
# 4 · run_id 幂等
# --------------------------------------------------------------------------------------


async def test_duplicate_run_id_replays_without_reapplying(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.6 幂等键：同一 run_id 重复提交返回上次结果，绝不重复应用（version 只 +1 一次）。"""
    db, fx = env
    fact_id = await _seed_fact(db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id)
    derived_id = uuid.uuid4().hex
    body = _request(
        fx,
        verdicts=[MergeVerdictItem(fact_id=fact_id, verdict='merged_into', judged_revision=1)],
        derived_facts=[
            MergeDerivedFactItem(
                fact_id=derived_id,
                predicate='咖啡偏好',
                object_json='"合成条"',
                subject_kind='owner',
                subject_id=fx.owner_id,
                merged_from=[fact_id],
            )
        ],
        owner_memory=MergeOwnerMemoryPayload(content='第一次'),
    )
    first = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    await db.commit()
    assert first.new_owner_memory_version == 1
    assert first.derived_created == 1

    # 主脑网络抖动重发同一份提交体（base 仍是它当初读到的 0）
    second = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    await db.commit()
    assert second.replayed is True
    assert second.applied is True
    assert second.new_owner_memory_version == 1  # 没有再 +1
    assert second.derived_created == 0
    assert second.portraits_updated == 0

    memory = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert memory.version == 1
    runs = (await db.execute(select(MergeRun).where(MergeRun.owner_id == fx.owner_id))).scalars().all()
    assert len(runs) == 1


# --------------------------------------------------------------------------------------
# 5 · 逐条失效护栏（§3.4）
# --------------------------------------------------------------------------------------


async def test_stale_verdict_skipped_others_applied(env: tuple[AsyncSession, Fixture]) -> None:
    """§3.4：judged_revision 与库中不等的裁决**跳过该条**，其余照常应用——不是整轮失败。"""
    db, fx = env
    fresh = await _seed_fact(db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id, revision=1)
    # 这条事实在主脑作出裁决之后又被本地整理过（revision 前进到 3）
    moved = await _seed_fact(
        db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id, revision=3, predicate='作息'
    )
    missing = uuid.uuid4().hex

    body = _request(
        fx,
        verdicts=[
            MergeVerdictItem(fact_id=fresh, verdict='merged_into', judged_revision=1),
            MergeVerdictItem(fact_id=moved, verdict='disputed', judged_revision=2),
            MergeVerdictItem(fact_id=missing, verdict='disputed', judged_revision=1),
        ],
    )
    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)

    assert result.applied is True
    skipped = {s.fact_id: s for s in result.skipped_verdicts}
    assert set(skipped) == {moved, missing}
    assert skipped[moved].reason == 'verdict_stale'
    assert skipped[moved].current_revision == 3
    assert skipped[missing].reason == 'fact_not_found'
    assert skipped[missing].current_revision is None

    assert (await _overlay(db, fresh))[0] == 'merged_into'
    assert (await _overlay(db, moved))[0] is None  # 过期裁决没落库


# --------------------------------------------------------------------------------------
# 6 · 整轮替换（D-4）
# --------------------------------------------------------------------------------------


async def test_second_run_replaces_previous_overlay(env: tuple[AsyncSession, Fixture]) -> None:
    """D-4：第二轮应用后第一轮的 overlay 被**清空**（整体替换），不是叠加。

    这是「上一轮错误裁决下一轮自动纠正」成立的前提：若旧裁决留着叠加，被错标 merged_into 的
    事实会一直不可见，错误永久化（D-17 明确点名的失败模式）。
    """
    db, fx = env
    old = await _seed_fact(db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id)
    new = await _seed_fact(
        db, owner_id=fx.owner_id, node_id=fx.node_a, agent_id=fx.master_agent_id, predicate='作息'
    )

    run1 = _request(fx, verdicts=[MergeVerdictItem(fact_id=old, verdict='merged_into', judged_revision=1)])
    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=run1)
    await db.commit()
    assert (await _overlay(db, old))[0] == 'merged_into'

    run2 = _request(
        fx,
        base_owner_memory_version=1,
        verdicts=[MergeVerdictItem(fact_id=new, verdict='disputed', judged_revision=1)],
    )
    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=run2)
    await db.commit()

    assert await _overlay(db, old) == (None, None, None)  # 上一轮整体作废
    assert (await _overlay(db, new)) == ('disputed', run2.run_id, 1)


# --------------------------------------------------------------------------------------
# 7 · 合并待办（§5.5）
# --------------------------------------------------------------------------------------


async def test_merge_request_dedup_keeps_latest(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.5：连续 3 次请求只留 1 条最新——待办去重不堆积，由 owner_id 主键在结构上钉死。"""
    db, fx = env
    for reason in ('local_review_done', 'owner_manual', 'purge_cascade'):
        await merge_gate_service.request_merge(
            db,
            owner_id=fx.owner_id,
            agent_id=fx.other_agent_id,
            body=MergeRequestBody(node_id=fx.node_b, reason=reason),
        )
    await db.flush()

    rows = (await db.execute(select(MergeRequest).where(MergeRequest.owner_id == fx.owner_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == 'purge_cascade'
    assert rows[0].consumed_time is None


async def test_merge_request_flags_master_brain_and_apply_consumes_it(env: tuple[AsyncSession, Fixture]) -> None:
    """待办由主脑下一轮成功合并时顺带消化（§5.5），非主脑请求 is_master_brain=False。"""
    db, fx = env
    registered = await merge_gate_service.request_merge(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.other_agent_id,
        body=MergeRequestBody(node_id=fx.node_b, reason='local_review_done'),
    )
    assert registered.accepted is True
    assert registered.is_master_brain is False
    assert registered.pending.requested_by_agent == fx.other_agent_id

    status_before = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert status_before.has_pending_request is True
    assert status_before.days_since_last_merge is None
    assert status_before.master_brain_agent_id == fx.master_agent_id
    assert status_before.master_brain_node_id == fx.node_a
    assert status_before.stale_threshold_days == 7

    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=_request(fx))
    await db.flush()

    status_after = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert status_after.has_pending_request is False
    assert status_after.last_merge_node_id == fx.node_a
    assert status_after.last_merge_agent_id == fx.master_agent_id
    assert status_after.days_since_last_merge is not None
    assert status_after.stale_over_threshold is False


async def test_merge_status_surfaces_rejection_reason(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.6 拒绝可解释：主人在记忆页看得到「上次为什么没整理成」，不是静默停摆。"""
    db, fx = env
    body = _request(fx, node_id=fx.node_b)
    with pytest.raises(MergeGateRejectedError):
        await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    await db.rollback()

    status = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert status.last_rejected_run_id == body.run_id
    assert status.last_rejected_reason == REJECT_NOT_MASTER_BRAIN
    assert status.last_merge_run_id is None


# --------------------------------------------------------------------------------------
# 8 · 退役回归（§8.5 / §10）
# --------------------------------------------------------------------------------------


async def test_contribute_no_longer_merges_inline(env: tuple[AsyncSession, Fixture]) -> None:
    """doc19 §10：contribute 只落贡献流，`owner_memory.version` 一动不动（不假装已合并）。"""
    db, fx = env
    accepted = await owner_memory_service.contribute(
        db, owner_id=fx.owner_id, agent_hasn_id=fx.master_agent_id, content='主人常驻昆明，注重健康'
    )
    await db.flush()
    assert accepted['accepted'] is True

    rows = (
        await db.execute(
            select(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.owner_id == fx.owner_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == 'pending'
    assert rows[0].merged_into_version is None

    memory = await owner_memory_service.get_owner_memory(db, owner_id=fx.owner_id)
    assert memory['version'] == 0
    assert memory['content'] is None


async def test_cloud_memory_semantic_processing_retired(env: tuple[AsyncSession, Fixture]) -> None:
    """§8.5：云端不再有任何记忆语义处理——退役必须真删代码，不许留死函数。

    留着死函数的代价不是洁癖问题：`merge_owner_memory` 只要还在 service 上，任何一次改动把它
    加回热路径就会与主脑合并双写同一份 `owner_memory`，而 CAS 只拦得住走合并闸的那一路。

    同时钉住**表不删、写者换人**（§10）：`owner_memory` / `peer_portrait` 继续是合并态存储与
    MEMPUSH 下发源，退役退的是语义处理，不是存储。
    """
    import importlib

    db, fx = env
    from backend.app.hasn_memory.service import owner_memory_service as om
    from backend.app.hasn_memory.service import peer_portrait_service as pp

    for name in ('merge_owner_memory', 'sweep_pending_merges'):
        assert not hasattr(om.owner_memory_service, name), f'{name} 未真正删除'
    for name in ('synthesize_peer_portrait', 'sweep_peer_portraits'):
        assert not hasattr(pp.peer_portrait_service, name), f'{name} 未真正删除'
    # 云端记忆语义处理的 LLM 入口一并消失（提示词构造函数也不留）
    for module, attr in ((om, '_merge_messages'), (om, '_default_llm_complete'),
                         (pp, '_synthesize_messages'), (pp, '_default_llm_complete')):
        assert not hasattr(module, attr), f'{attr} 未真正删除'
    # celery 面整体消失（模块都不在了，beat 无从引用）
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('backend.app.hasn_memory.tasks')

    # 表仍在且仍可被合并闸写入（写者换人，不是把存储一起退掉）
    await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.master_agent_id,
        body=_request(
            fx,
            owner_memory=MergeOwnerMemoryPayload(content='存储仍在'),
            peer_portraits=[
                MergePeerPortraitItem(peer_hasn_id=f'h_peer{uuid.uuid4().hex[:16]}', portrait_text='画像仍可写入')
            ],
        ),
    )
    await db.flush()
    assert (
        await db.execute(select(sa.func.count()).select_from(PeerPortrait).where(PeerPortrait.owner_id == fx.owner_id))
    ).scalar_one() == 1


# --------------------------------------------------------------------------------------
# 12 · 退化输入：`owner_memory` 缺键 / 三个数组为空（合并闸必须原样接住）
# --------------------------------------------------------------------------------------
#
# 本地 `hasn.memory.merge` 在**未重算画像**时会**整个省略** `owner_memory` 键——绝不把上一版
# 正文再交一遍冒充「重算过了」，这是零 fake 的正确做法（构造点：hasn-mcp/src/memory.rs 的
# `merge_apply_request`「画像没重算就不带这个键」）。云端若因此报错或把旧正文当新版本入库，
# 合并就会在「本轮没什么可改」的最常见情形下整轮失败，而症状只表现为「主脑很久没整理了」。


async def test_missing_owner_memory_key_keeps_content_and_advances_version(
    env: tuple[AsyncSession, Fixture],
) -> None:
    """缺键 = 本轮没重算画像：跳过画像更新、**不覆盖已有正文**、`version` 照常推进。"""
    db, fx = env
    # 先跑一轮带正文的，把库里种上一版真实正文与 owner_edited 复位状态
    first = _request(fx, owner_memory=MergeOwnerMemoryPayload(content='健康: 主人注重抗衰老'))
    await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=first)
    seeded = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert seeded.version == 1
    content_before = seeded.content
    token_count_before = seeded.token_count
    assert content_before

    # 第二轮：请求体里**根本没有** owner_memory 这个键（与真实线上载荷同形）
    raw: dict[str, Any] = {
        'run_id': f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': fx.node_a,
        'base_owner_memory_version': 1,
        'verdicts': [],
        'derived_facts': [],
        'peer_portraits': [],
        'summary': '本轮没有需要改的画像',
        'stats': {'facts_judged': 3, 'facts_merged': 0, 'facts_disputed': 0},
    }
    body = MergeApplyRequest.model_validate(raw)
    assert body.owner_memory is None, '缺键必须解析成 None，而不是 422'

    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    assert result.applied is True
    assert result.new_owner_memory_version == 2

    # 合并闸走裸 SQL 写库，ORM identity map 里那份仍是旧值——不 expire 就会读到自己种下的旧行。
    db.expire_all()
    after = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert after.version == 2, 'version 是轮次水位，正文没变也必须 +1（否则下轮 CAS 恒冲突）'
    assert after.content == content_before, '缺键绝不覆盖已有正文'
    assert after.token_count == token_count_before
    assert after.last_merge_run_id == body.run_id
    assert after.last_merge_summary == '本轮没有需要改的画像'


async def test_missing_owner_memory_key_does_not_reset_owner_edited(
    env: tuple[AsyncSession, Fixture],
) -> None:
    """§4.6：`owner_edited` 只在本轮**真的重算并消费了**主人手工版本时才复位。

    缺键轮把它一起复位，等于宣称「主人的手工改动已被本轮重算吸收」——那是纯粹的谎报，
    下一轮重算的 prompt 就不会再携带主人版本，手工编辑被静默冲掉。
    """
    db, fx = env
    await db.execute(
        pg_insert(HasnOwnerMemory)
        .values(owner_id=fx.owner_id, content='主人手改的正文', version=3, owner_edited=True)
        .on_conflict_do_nothing(index_elements=['owner_id'])
    )
    await db.flush()

    body = MergeApplyRequest.model_validate({
        'run_id': f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': fx.node_a,
        'base_owner_memory_version': 3,
    })
    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    assert result.new_owner_memory_version == 4

    db.expire_all()
    row = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert row.owner_edited is True, '本轮没重算画像，主人手工标位不得被复位'
    assert row.content == '主人手改的正文'


async def test_fully_degenerate_round_applies_cleanly(env: tuple[AsyncSession, Fixture]) -> None:
    """全退化输入（缺 owner_memory + 三个数组皆空 + 无 summary）仍是一轮**成功**的合并。

    「本轮读了 N 条事实、一条都不用改」是最常见的结果，它必须能推进 `version` 并留痕，
    否则主脑下一轮拿着旧基线永远 version_conflict，记忆页永远显示「很久没整理了」。
    """
    db, fx = env
    body = MergeApplyRequest.model_validate({
        'run_id': f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': fx.node_a,
        'base_owner_memory_version': 0,
    })
    assert body.verdicts == []
    assert body.derived_facts == []
    assert body.peer_portraits == []
    assert body.owner_memory is None
    assert body.summary is None
    assert (body.stats.facts_judged, body.stats.facts_merged, body.stats.facts_disputed) == (0, 0, 0)

    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
    assert result.applied is True
    assert result.replayed is False
    assert result.new_owner_memory_version == 1
    assert result.skipped_verdicts == []
    assert result.derived_created == 0
    assert result.portraits_updated == 0

    memory = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    assert memory.version == 1
    assert memory.content is None, '从未有过正文的 owner 首轮空跑不该凭空造出正文'
    assert memory.last_merge_run_id == body.run_id

    run_row = (await db.execute(select(MergeRun).where(MergeRun.run_id == body.run_id))).scalar_one()
    assert run_row.status == 'applied'
    assert run_row.reject_reason is None
    assert (
        await db.execute(select(sa.func.count()).select_from(PeerPortrait).where(PeerPortrait.owner_id == fx.owner_id))
    ).scalar_one() == 0


async def test_empty_owner_memory_content_is_treated_as_no_recompute(
    env: tuple[AsyncSession, Fixture],
) -> None:
    """`owner_memory={'content': None}` / 空白串与缺键同档：都不覆盖已有正文。

    本地真到了「重算出空正文」这一步也不该覆盖——空正文不是画像，是失败的重算。
    """
    db, fx = env
    await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.master_agent_id,
        body=_request(fx, owner_memory=MergeOwnerMemoryPayload(content='工作: 主人主攻 Rust')),
    )
    seeded = (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
    ).scalar_one()
    content_before = seeded.content

    for index, payload in enumerate((MergeOwnerMemoryPayload(content=None), MergeOwnerMemoryPayload(content='   '))):
        body = _request(fx, base_owner_memory_version=1 + index, owner_memory=payload)
        result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.master_agent_id, body=body)
        assert result.new_owner_memory_version == 2 + index
        db.expire_all()
        row = (
            await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == fx.owner_id))
        ).scalar_one()
        assert row.content == content_before, f'第 {index} 种空正文形态覆盖了已有正文'
        assert row.version == 2 + index


# --------------------------------------------------------------------------------------
# 13 · 跨仓契约钉子：合并提交体字段名（本地 Rust 构造点 ↔ 云端 Pydantic）
# --------------------------------------------------------------------------------------
#
# S5 已有「云端事件白名单 vs 本地 Rust 常量」的钉子，合并闸的**提交体字段名**此前没有同类
# 保护。字段名单边漂移不会报错也不会 5xx——云端 Pydantic 直接忽略未知键、必填键缺失则 422，
# 两种结局在 §5.5 的可见性面上都只显示成「主脑很久没整理了」，排查成本极高。

#: 本地合并提交体的构造点（`hasn.memory.merge` 工具组请求体）。
_LOCAL_MERGE_TOOL_RS = Path('crates/hasn-mcp/src/memory.rs')
#: 本地合并闸传输层（应答结构体在这里）。
_LOCAL_MERGE_TRANSPORT_RS = Path('crates/hasn-node/src/backend/modules/huanxing_agent/memory.rs')


def _strip_line_comments(body: str) -> str:
    """去掉 ``//`` 行注释，避免注释里的引号被当成 JSON 键扫进来。"""
    return re.sub(r'//[^\n]*', '', body)


def _rust_fn_body(source: str, name: str) -> str:
    """按花括号配平截出一个 Rust 函数体（正则截不准嵌套 ``json!`` 块）。"""
    start = source.find(f'fn {name}(')
    assert start != -1, f'找不到 fn {name}，两侧对照点已漂移'
    brace = source.find('{', start)
    assert brace != -1, f'fn {name} 没有函数体'
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f'fn {name} 花括号不配平')


def _rust_struct_fields(source: str, name: str) -> set[str]:
    """取一个 ``pub struct`` 的 pub 字段名（serde 默认按字段名序列化，本仓两侧无 rename）。"""
    start = source.find(f'pub struct {name} ')
    assert start != -1, f'找不到 pub struct {name}，两侧对照点已漂移'
    brace = source.find('{', start)
    end = source.find('\n}', brace)
    assert end != -1, f'struct {name} 没有结束花括号'
    block = _strip_line_comments(source[brace:end])
    return set(re.findall(r'pub\s+([a-z_][a-z0-9_]*)\s*:', block))


def _json_keys(block: str) -> set[str]:
    """扫一段 Rust 代码里出现的 JSON 键：``"key":`` 与 ``request["key"] =`` 两种写法都算。"""
    clean = _strip_line_comments(block)
    return set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', clean)) | set(
        re.findall(r'\[\s*"([a-z_][a-z0-9_]*)"\s*\]\s*=', clean)
    )


#: 云端提交体涉及的全部 DTO（合并提交体是嵌套结构，字段名要按并集比）。
_MERGE_REQUEST_SCHEMAS = (
    MergeApplyRequest,
    MergeVerdictItem,
    MergeDerivedFactItem,
    MergeOwnerMemoryPayload,
    MergePeerPortraitItem,
    MergeStats,
)


async def test_merge_request_body_field_names_match_local_rust_builder() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """合并提交体字段名两侧必须**完全一致**（多一个少一个都要显式改这条）。

    对照点：``hasn-mcp/src/memory.rs::merge_apply_request`` + ``stats_json``（本地唯一构造点，
    注释原文就写着「字段名与云端切片同一份，不得擅改」）↔ ``schema/merge_gate.py`` 的六个 DTO。
    """
    source_path = find_local_rust_source(_LOCAL_MERGE_TOOL_RS)
    if source_path is None:
        pytest.skip(
            '本机没有并排的 hasn-node 检出（云端 CI 不检出该仓），跳过跨仓字段名比对；'
            '本条只在两仓并排的开发机上有意义，那正是引入不对称的现场'
        )
    source = source_path.read_text(encoding='utf-8')
    local_keys = _json_keys(_rust_fn_body(source, 'merge_apply_request')) | _json_keys(
        _rust_fn_body(source, 'stats_json')
    )
    cloud_fields = {field for schema in _MERGE_REQUEST_SCHEMAS for field in schema.model_fields}

    assert local_keys == cloud_fields, (
        f'合并提交体字段名两侧漂移（症状只会表现成「主脑很久没整理了」）：'
        f'本地多出={sorted(local_keys - cloud_fields)} 云端多出={sorted(cloud_fields - local_keys)}；'
        f'本地源文件={source_path}'
    )


async def test_merge_response_field_names_are_a_subset_of_cloud_schema() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """本地应答结构体的字段名必须是云端应答 DTO 的子集（本地少读几个字段是允许的）。

    对照点：``huanxing_agent/memory.rs::MergeApplyResponse`` ↔ ``schema/merge_gate.py`` 同名 DTO。
    本地多出的字段名恒为 ``None`` / 零值，主脑据此汇报的「云端建了几条派生事实」会全是 0——
    比字段名对不上更难发现。
    """
    source_path = find_local_rust_source(_LOCAL_MERGE_TRANSPORT_RS)
    if source_path is None:
        pytest.skip('本机没有并排的 hasn-node 检出（云端 CI 不检出该仓），跳过跨仓应答字段名比对')
    source = source_path.read_text(encoding='utf-8')
    local_fields = _rust_struct_fields(source, 'MergeApplyResponse')
    cloud_fields = set(MergeApplyResponse.model_fields)
    assert local_fields, f'{source_path} 里没解析出 MergeApplyResponse 字段，对照点已漂移'
    assert local_fields <= cloud_fields, (
        f'本地读了云端不发的应答字段（恒为默认值，主脑汇报会静默失真）：'
        f'{sorted(local_fields - cloud_fields)}；本地源文件={source_path}'
    )
