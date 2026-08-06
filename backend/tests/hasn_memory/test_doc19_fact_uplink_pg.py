"""doc19 S5-cloud · 语义事实上行 apply 的真实 PG 验收（零 mock）。

设计事实源：``docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md``
事故教训：``实施/98-记忆上推链路整体退役与本地镜像单向化实施方案.md``

**本文件逐条覆盖 §8.3 六条硬约束**（每个用例的 docstring 标注对应条目）：

1. 上行的是业务事件、幂等键 ``(owner, node, fact_id, revision)``
   → ``test_idempotent_replay_*`` / ``test_revision_replay_*``
2. 每条脏行都有待推 op（**本地侧**不变量，云端无对应面）
   → 云端侧对应「不重复推进 namespace_revision」，见 ``test_idempotent_replay_*``
3. 回灌片永不进 outbox / 下行只写 overlay 不写自产片业务字段组
   → ``test_merge_verdict_only_touches_overlay`` + ``test_foreign_node_cannot_edit_own_slice``
4. 拒绝按实施/98 契约逐事件处置（永久拒绝丢弃 + 一次性 warn，冲突退避，禁止无退避重推）
   → ``test_origin_mismatch_*`` / ``test_update_before_save_is_retryable_conflict`` /
     ``test_permanent_rejection_warns_once_not_error``
5. 上行事件类型必须两侧白名单对称 → ``test_uplink_whitelist_matches_local_rust_constants``
6. purge 墓碑防复活 + 回令来源节点清理 → ``test_tombstone_hit_*`` / ``test_purge_cascades_*``

另有一组 §4.6 / D-20 **主人第三类写者**：载荷带 ``owner_write`` 时豁免 origin 守卫、改判归属主人
→ ``test_owner_write_*``（十一节）。

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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.model.fact_tombstone import FactTombstone
from backend.app.hasn_memory.model.merge_request import MergeRequest
from backend.app.hasn_memory.service.fact_uplink_service import (
    MEMORY_FACT_PERMANENT_CODES,
    MEMORY_FACT_UPLINK_ERROR_CODES,
    MEMORY_FACT_UPLINK_EVENTS,
    OWNER_WRITE_MARKER,
    FactUplinkConflictError,
    FactUplinkPermanentError,
    _parse_fact,
    fact_uplink_service,
    reset_warn_once_cache,
)
from backend.common.log import log
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.database.schema_names import SCHEMA_NAMES
from backend.tests.hasn_memory.local_rust_source import find_local_rust_source

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')


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
    reset_warn_once_cache()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        # 全局 async_db_session 池绑上一事件循环；每测试后释放，避免下个测试 different loop。
        await async_engine.dispose()


# --------------------------------------------------------------------------------------
# 夹具：构造与本地 `fact_snapshot_json`（hasn-memory/src/storage/facts.rs）**逐字段同形**的载荷
# --------------------------------------------------------------------------------------


def _snapshot(
    *,
    owner_id: str,
    node_id: str,
    agent_id: str,
    fact_id: str,
    revision: int = 1,
    subject_kind: str = 'agent_self',
    predicate: str = '偏好',
    obj: Any = '喜欢冰美式',
    status: str = 'active',
    superseded_by: str | None = None,
    merged_from: list[str] | None = None,
    origin_node_id: str | None = None,
    origin_kind: str = 'node',
    valid_until: int | None = None,
    rationale: str | None = None,
    confidence: float = 0.8,
    supersedes_hint: str | None = None,
) -> dict[str, Any]:
    """本地 outbox 快照载荷。字段名与 hasn-node 侧 ``fact_snapshot_json`` 严格一致。"""
    is_agent_self = subject_kind == 'agent_self'
    return {
        'fact_id': fact_id,
        'owner_id': owner_id,
        'agent_id': agent_id if is_agent_self else None,
        'subject_kind': subject_kind,
        'subject_id': agent_id if is_agent_self else owner_id,
        'scope_kind': 'global',
        'scope_id': agent_id if is_agent_self else owner_id,
        'predicate': predicate,
        'object_json': obj,
        'confidence': confidence,
        'status': status,
        'superseded_by': superseded_by,
        'source_turn_ids': [],
        'source_refs': [],
        'rationale': rationale,
        'valid_until': valid_until,
        # doc19 §4.3 / §8.2 双端列。本地对照点：
        # `hasn-node/crates/hasn-memory/src/storage/facts.rs::fact_snapshot_json`
        'supersedes_hint': supersedes_hint,
        'created_at': 1_785_000_000_000,
        'updated_at': 1_785_000_000_000 + revision,
        'revision': revision,
        'origin_kind': origin_kind,
        'origin_node_id': origin_node_id if origin_node_id is not None else node_id,
        'origin_agent_id': agent_id,
        'merged_from': merged_from or [],
    }


async def _apply(
    session: AsyncSession,
    *,
    owner_id: str,
    node_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    out = await fact_uplink_service.apply_fact_event(
        session, owner_id=owner_id, node_id=node_id, event_type=event_type, payload=payload
    )
    await session.commit()
    return out


async def _row(session: AsyncSession, fact_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            sa.text(
                'SELECT fact_id, owner_id, agent_id, subject_kind, subject_id, scope_kind, scope_id, '
                'predicate, object_json, confidence, status, superseded_by, rationale, valid_until, '
                'supersedes_hint, '
                'updated_at, origin_kind, origin_node_id, origin_agent_id, merged_from, revision, '
                'merge_verdict, merge_verdict_run, merge_judged_revision '
                'FROM hasn_memory.semantic_fact WHERE fact_id = :fact_id'
            ),
            {'fact_id': fact_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _namespace_revision(session: AsyncSession, *, scope_kind: str, scope_id: str, namespace: str) -> int:
    value = (
        await session.execute(
            sa.text(
                'SELECT revision FROM hasn_memory.namespace_revision '
                'WHERE sync_scope_kind = :k AND sync_scope_id = :i AND namespace = :n'
            ),
            {'k': scope_kind, 'i': scope_id, 'n': namespace},
        )
    ).scalar_one_or_none()
    return int(value or 0)


async def _downlink_events(session: AsyncSession, owner_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            sa.text(
                f'SELECT event_type, aggregate_id, payload FROM {_SYNC_EVENTS} '
                "WHERE owner_id = :owner_id AND event_type LIKE 'memory.%' ORDER BY revision ASC"
            ),
            {'owner_id': owner_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _cleanup(session: AsyncSession, owner_id: str, agent_id: str) -> None:
    await session.rollback()
    await session.execute(
        sa.text('DELETE FROM hasn_memory.semantic_fact WHERE owner_id = :owner_id'), {'owner_id': owner_id}
    )
    await session.execute(delete(FactTombstone).where(FactTombstone.owner_id == owner_id))
    await session.execute(delete(MergeRequest).where(MergeRequest.owner_id == owner_id))
    await session.execute(
        sa.text(f'DELETE FROM {_SYNC_EVENTS} WHERE owner_id = :owner_id'),
        {'owner_id': owner_id},
    )
    await session.execute(
        sa.text(
            'DELETE FROM hasn_memory.namespace_revision WHERE sync_scope_id = :owner_id OR sync_scope_id = :agent_id'
        ),
        {'owner_id': owner_id, 'agent_id': agent_id},
    )
    await session.commit()


class _Ids:
    """一组一次性标识，避免各用例互相踩数据。"""

    def __init__(self) -> None:
        self.owner = f'h_up{uuid.uuid4().hex[:16]}'
        self.agent = f'a_up{uuid.uuid4().hex[:16]}'
        self.node = f'node_{uuid.uuid4().hex[:12]}'
        self.other_node = f'node_{uuid.uuid4().hex[:12]}'
        self.fact = uuid.uuid4().hex


# --------------------------------------------------------------------------------------
# 一、六个事件各自 apply 成功并落对应列
# --------------------------------------------------------------------------------------


async def test_six_uplink_events_each_land_expected_columns(session: AsyncSession) -> None:
    """§8.3-1：六个业务事件逐个 apply，各自落到该落的列上。"""
    ids = _Ids()
    try:
        # 1) saved：插入业务字段组 + 溯源四列 + revision=1
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, rationale='现场记下'
            ),
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['origin_kind'] == 'node'
        assert row['origin_node_id'] == ids.node
        assert row['origin_agent_id'] == ids.agent
        assert row['revision'] == 1
        assert row['status'] == 'active'
        assert row['rationale'] == '现场记下'
        assert row['merge_verdict'] is None

        # 2) updated：业务字段组 + revision 前进
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=2,
                obj='改喝手冲',
                confidence=0.9,
                valid_until=4_102_444_800_000,
            ),
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['revision'] == 2
        assert '手冲' in row['object_json']
        assert row['confidence'] == pytest.approx(0.9)
        assert row['valid_until'] == 4_102_444_800_000

        # 3) superseded：status + superseded_by
        newer = uuid.uuid4().hex
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=newer, predicate='偏好', obj='拿铁'
            ),
        )
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.superseded',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=3,
                status='superseded',
                superseded_by=newer,
            ),
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['status'] == 'superseded'
        assert row['superseded_by'] == newer
        assert row['revision'] == 3

        # 4) withdrawn：软删
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.withdrawn',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=4,
                status='withdrawn',
            ),
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['status'] == 'withdrawn'
        assert row['revision'] == 4

        # 5) merge_verdict：只写 overlay 三列
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.merge_verdict',
            payload={
                'fact_id': ids.fact,
                'merge_verdict': 'disputed',
                'merge_verdict_run': 'run_a',
                'merge_judged_revision': 4,
            },
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert (row['merge_verdict'], row['merge_verdict_run'], row['merge_judged_revision']) == (
            'disputed',
            'run_a',
            4,
        )

        # 6) purged：物理删除 + 墓碑
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.owner, 'reason': 'owner_purge'},
        )
        assert await _row(session, ids.fact) is None
        tomb = (
            await session.execute(select(FactTombstone).where(FactTombstone.fact_id == ids.fact))
        ).scalar_one()
        assert tomb.owner_id == ids.owner
        assert tomb.purged_by == ids.owner
        assert tomb.cascade_from is None
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 二、幂等（§8.3-1 幂等键 (owner, node, fact_id, revision)）
# --------------------------------------------------------------------------------------


async def test_idempotent_replay_keeps_row_and_namespace_revision_unchanged(session: AsyncSession) -> None:
    """同一 ``(owner, node, fact_id, revision)`` 推两次：第二次「已应用」，库不变、游标不再推进。

    「不重复推进 namespace_revision」是云端侧对 §8.3-2 的对偶保证：重放若照样推进游标，
    每个节点都会白拉一条无意义回灌事件，量大时等价于制造一条自增的噪声流。
    """
    ids = _Ids()
    payload = _snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact)
    try:
        first = await _apply(
            session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.saved', payload=payload
        )
        assert first['outcome'] == 'applied'
        after_first = await _namespace_revision(
            session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts'
        )
        row_first = await _row(session, ids.fact)

        second = await _apply(
            session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.saved', payload=payload
        )
        assert second['outcome'] == 'replay', '同一幂等键重复提交必须返回「已应用」而不是报错'
        after_second = await _namespace_revision(
            session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts'
        )
        assert after_second == after_first, '重放不得重复推进 namespace_revision'
        assert await _row(session, ids.fact) == row_first, '重放不得改动库里的行'
        assert len(await _downlink_events(session, ids.owner)) == 1, '重放不得再造一条回灌事件'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_merge_verdict_replay_is_idempotent(session: AsyncSession) -> None:
    """overlay 完全等值的 merge_verdict 重复提交同样按「已应用」处置，不推进游标。"""
    ids = _Ids()
    verdict = {
        'fact_id': ids.fact,
        'merge_verdict': 'merged_into',
        'merge_verdict_run': 'run_x',
        'merge_judged_revision': 1,
    }
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        assert (
            await _apply(
                session,
                owner_id=ids.owner,
                node_id=ids.node,
                event_type='memory.fact.merge_verdict',
                payload=verdict,
            )
        )['outcome'] == 'applied'
        baseline = await _namespace_revision(
            session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts'
        )
        again = await _apply(
            session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.merge_verdict', payload=verdict
        )
        assert again['outcome'] == 'replay'
        assert (
            await _namespace_revision(session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts')
            == baseline
        )
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 三、§3.4 单一写者钉子：merge_verdict 只动 overlay
# --------------------------------------------------------------------------------------


async def test_merge_verdict_only_touches_overlay(session: AsyncSession) -> None:
    """§3.4 / §8.3-3：合并只写 overlay 字段组，业务字段与 revision **分毫未变**。

    这条一旦变红，主脑就成了业务状态的第二写者：它对他节点自产片的修改在同步模型里无路可落，
    且被错误裁决的事实会从下一轮全量重算的输入里消失、错误被永久化（doc19 D-17 与 D-4 直接矛盾）。
    """
    ids = _Ids()
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                rationale='合并前的依据',
                valid_until=4_102_444_800_000,
            ),
        )
        before = await _row(session, ids.fact)
        assert before is not None

        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.merge_verdict',
            payload={
                'fact_id': ids.fact,
                'merge_verdict': 'merged_into',
                'merge_verdict_run': 'run_1',
                'merge_judged_revision': 1,
            },
        )
        after = await _row(session, ids.fact)
        assert after is not None

        business_columns = (
            'predicate',
            'object_json',
            'confidence',
            'status',
            'superseded_by',
            'rationale',
            'valid_until',
            'updated_at',
            'revision',
            'origin_kind',
            'origin_node_id',
            'origin_agent_id',
            'merged_from',
        )
        for column in business_columns:
            assert after[column] == before[column], f'merge_verdict 不得触碰业务字段：{column}'
        assert after['revision'] == 1, 'overlay 写入绝不推进 revision'
        assert after['merge_verdict'] == 'merged_into'
        assert after['merge_judged_revision'] == 1
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 四、revision 单调守卫（重放丢弃且是 warn 不是 error；跳号接受）
# --------------------------------------------------------------------------------------


async def test_revision_replay_is_dropped_with_warn_not_error(session: AsyncSession) -> None:
    """§8.3-4：收到的 revision ≤ 现值 → 重放丢弃，日志等级必须是 ``warn``。

    实施/98 的 94% 日志洪水正是「可恢复情形被当成终局故障反复刷屏」。这里钉死：整个重放
    路径不许出现任何 ERROR 记录。
    """
    ids = _Ids()
    records: list[tuple[str, str]] = []
    sink_id = log.add(lambda message: records.append((message.record['level'].name, message.record['message'])))
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2, obj='新值'
            ),
        )
        records.clear()

        stale = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=1, obj='旧值'
            ),
        )
        assert stale['outcome'] == 'replay'
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['revision'] == 2, '旧 revision 不得回卷库里的行'
        assert '新值' in row['object_json'], '重放不得把旧正文写回去'

        levels = {level for level, _ in records}
        assert 'ERROR' not in levels, f'revision 重放必须是 warn 不是 error：{records}'
        assert any(level == 'WARNING' and 'revision 重放' in text for level, text in records), records
    finally:
        log.remove(sink_id)
        await _cleanup(session, ids.owner, ids.agent)


async def test_revision_gap_is_accepted_with_warn(session: AsyncSession) -> None:
    """跳号（> 现值 + 1）仍然接受——本地可能有未上行的中间态；拒绝会让那条事实永远推不上来。"""
    ids = _Ids()
    records: list[tuple[str, str]] = []
    sink_id = log.add(lambda message: records.append((message.record['level'].name, message.record['message'])))
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        records.clear()
        out = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=7, obj='离线攒的'
            ),
        )
        assert out['outcome'] == 'applied'
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['revision'] == 7
        assert {level for level, _ in records} <= {'WARNING', 'INFO', 'DEBUG'}
        assert any('跳号' in text for _, text in records), records
    finally:
        log.remove(sink_id)
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 五、origin 守卫（§3.3 / §8.3-3）
# --------------------------------------------------------------------------------------


async def test_origin_mismatch_on_saved_is_permanently_rejected(session: AsyncSession) -> None:
    """§8.3-3：``saved`` 的 ``origin_node_id`` 必须与推送节点一致，否则**永久拒绝**。

    节点只能上行自己的自产片——放行就等于让镜像行被盲目回传，正是实施/98 的病根。
    """
    ids = _Ids()
    payload = _snapshot(
        owner_id=ids.owner,
        node_id=ids.node,
        agent_id=ids.agent,
        fact_id=ids.fact,
        origin_node_id=ids.other_node,
    )
    try:
        decision = await fact_uplink_service.classify(
            session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.saved', payload=payload
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8044
        assert decision.error.code in MEMORY_FACT_PERMANENT_CODES

        with pytest.raises(FactUplinkPermanentError):
            await fact_uplink_service.apply_fact_event(
                session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.saved', payload=payload
            )
        await session.rollback()
        assert await _row(session, ids.fact) is None
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_merged_slice_cannot_be_uplinked_as_own_slice(session: AsyncSession) -> None:
    """§3.2：``merged`` / ``retired`` 片只由合并维护，任何节点都不得当自产片上行。"""
    ids = _Ids()
    try:
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                origin_kind='merged',
                origin_node_id=ids.node,
            ),
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8044
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_foreign_node_cannot_edit_own_slice(session: AsyncSession) -> None:
    """§4.1 权限矩阵：整理只对本节点自产片生效；他节点整理一律永久拒绝。"""
    ids = _Ids()
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.withdrawn',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.other_node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=2,
                status='withdrawn',
                origin_node_id=ids.other_node,
            ),
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8044
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['status'] == 'active', '他节点的整理不得落地'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_update_before_save_is_retryable_conflict(session: AsyncSession) -> None:
    """§8.3-4：事实尚未汇聚时的整理事件是**冲突**（可退避重试），不是永久拒绝。

    判成永久就等于把一次乱序推送变成永久丢一条记忆。
    """
    ids = _Ids()
    try:
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2
            ),
        )
        assert decision.outcome == 'reject_conflict'
        assert decision.error is not None
        assert decision.error.code == 8041
        assert decision.error.code not in MEMORY_FACT_PERMANENT_CODES

        with pytest.raises(FactUplinkConflictError):
            await fact_uplink_service.apply_fact_event(
                session,
                owner_id=ids.owner,
                node_id=ids.node,
                event_type='memory.fact.updated',
                payload=_snapshot(
                    owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2
                ),
            )
        await session.rollback()
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_invalid_payload_is_permanently_rejected(session: AsyncSession) -> None:
    """§8.3-4：schema 非法 → 永久拒绝（8043），让本地丢弃出队而不是无休止重推。"""
    ids = _Ids()
    broken = _snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact)
    broken['subject_kind'] = 'alien'
    try:
        decision = await fact_uplink_service.classify(
            session, owner_id=ids.owner, node_id=ids.node, event_type='memory.fact.saved', payload=broken
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8043
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_purge_from_non_owner_is_permanently_rejected(session: AsyncSession) -> None:
    """§4.1：硬删只有主人可发起；分身只能软删。冒名 purge 永久拒绝。"""
    ids = _Ids()
    try:
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.agent},
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8043
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 六、墓碑防复活（§4.5 / §8.3-6）
# --------------------------------------------------------------------------------------


async def test_tombstone_hit_permanently_rejects_and_orders_purge_local(session: AsyncSession) -> None:
    """§8.3-6：已 purge 的 fact 的晚到上行事件**永久拒绝**，并回令来源节点清理本地行与残留 op。

    离线节点重新上线时 outbox 里还压着 purge 之前的事件，放行就是让已删内容复活。
    """
    ids = _Ids()
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.owner},
        )

        # 离线节点重新上线，推来 purge 之前积压的 updated
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2
            ),
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8045
        assert decision.error.detail == {'action': 'purge_local', 'fact_id': ids.fact}
        assert await _row(session, ids.fact) is None, '被拒的晚到事件不得复活已删事实'

        # 同一条 purge 广播重复到达是幂等重放，不是拒绝
        replay = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.owner},
        )
        assert replay.outcome == 'replay'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_permanent_rejection_warns_once_not_every_retry(session: AsyncSession) -> None:
    """§8.3-4「一次性 warn」：同一条毒丸重复推送只记一条日志，且等级恒为 ``warn``。

    实施/98 的直接症状就是每 5s 一条 WARN 刷满 94% 日志、真实告警被淹没。
    """
    ids = _Ids()
    records: list[tuple[str, str]] = []
    sink_id = log.add(lambda message: records.append((message.record['level'].name, message.record['message'])))
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.owner},
        )
        records.clear()

        late = _snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2)
        for _ in range(5):
            decision = await fact_uplink_service.classify(
                session,
                owner_id=ids.owner,
                node_id=ids.node,
                event_type='memory.fact.updated',
                payload=late,
            )
            assert decision.outcome == 'reject_permanent'

        tombstone_warns = [text for level, text in records if level == 'WARNING' and '命中墓碑' in text]
        assert len(tombstone_warns) == 1, f'毒丸只许 warn 一次，实测 {len(tombstone_warns)} 次：{records}'
        assert 'ERROR' not in {level for level, _ in records}, records
    finally:
        log.remove(sink_id)
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 七、purge 级联（§4.5 / D-19）
# --------------------------------------------------------------------------------------


async def test_purge_cascades_three_generations_with_tombstones_and_merge_request(
    session: AsyncSession,
) -> None:
    """A ← B ← C 三级血缘：purge A 后三条全删、三条墓碑齐全、画像重算待办已登记。

    「不留任何内容」不做级联就是空话——派生事实的正文里可能原样含着被删信息。
    """
    ids = _Ids()
    fact_a = uuid.uuid4().hex
    fact_b = uuid.uuid4().hex
    fact_c = uuid.uuid4().hex
    bystander = uuid.uuid4().hex
    try:
        for fact_id in (fact_a, bystander):
            await _apply(
                session,
                owner_id=ids.owner,
                node_id=ids.node,
                event_type='memory.fact.saved',
                payload=_snapshot(
                    owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=fact_id, predicate='原始'
                ),
            )
        # B 由 A 合并派生，C 由 B 合并派生（合并派生事实由合并写入，此处直接落库模拟已回灌的派生片）
        for fact_id, lineage in ((fact_b, [fact_a]), (fact_c, [fact_b])):
            await session.execute(
                sa.text(
                    'INSERT INTO hasn_memory.semantic_fact ('
                    'fact_id, owner_id, agent_id, subject_kind, subject_id, memory_layer, scope_kind, scope_id, '
                    'predicate, object_json, confidence, status, source_turn_ids, source_refs_json, '
                    'created_at, updated_at, origin_kind, merged_from, revision'
                    ") VALUES (:fact_id, :owner_id, NULL, 'owner', :owner_id, 'semantic', 'global', :owner_id, "
                    ":predicate, :object_json, 0.9, 'active', '[]', '[]', 1, 1, 'merged', :merged_from, 1)"
                ),
                {
                    'fact_id': fact_id,
                    'owner_id': ids.owner,
                    'predicate': '派生',
                    'object_json': '"合并产物"',
                    'merged_from': f'["{lineage[0]}"]',
                },
            )
        # 旁观者指向 A（自引用连带修复的验收点）
        await session.execute(
            sa.text('UPDATE hasn_memory.semantic_fact SET superseded_by = :a WHERE fact_id = :b'),
            {'a': fact_a, 'b': bystander},
        )
        await session.commit()

        out = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={
                'fact_id': fact_a,
                'owner_id': ids.owner,
                'purged_by': ids.owner,
                'reason': 'owner_purge',
            },
        )
        assert set(out['purged']) == {fact_a, fact_b, fact_c}
        assert set(out['cascade']) == {fact_b, fact_c}

        for fact_id in (fact_a, fact_b, fact_c):
            assert await _row(session, fact_id) is None, f'{fact_id} 必须被物理删除'
        surviving = await _row(session, bystander)
        assert surviving is not None, '无血缘关系的旁观事实不得被误删'
        assert surviving['superseded_by'] is None, '指向被删行的自引用必须置空'
        assert surviving['revision'] == 1, '连带修复不是业务整理，不得推进 revision'

        tombstones = {
            row.fact_id: row.cascade_from
            for row in (
                await session.execute(select(FactTombstone).where(FactTombstone.owner_id == ids.owner))
            ).scalars()
        }
        assert set(tombstones) == {fact_a, fact_b, fact_c}
        assert tombstones[fact_a] is None
        assert tombstones[fact_b] == fact_a
        assert tombstones[fact_c] == fact_b

        pending = (
            await session.execute(select(MergeRequest).where(MergeRequest.owner_id == ids.owner))
        ).scalar_one()
        assert pending.reason == 'purge_cascade', '画像重算待办必须登记（合并态正文可能仍含被删内容）'
        assert pending.consumed_time is None

        # 下行必须广播 purge 让其余节点物理删除；载荷绝不含被删内容
        downlink = await _downlink_events(session, ids.owner)
        purge_events = [e for e in downlink if e['event_type'] == 'memory.fact.purged']
        assert {e['aggregate_id'] for e in purge_events} == {fact_a, fact_b, fact_c}
        for event in purge_events:
            assert set(event['payload']) <= {
                'fact_id',
                'purged_by',
                'cascade_from',
                'reason',
                'purged_at',
                'owner_id',
                'sync_scope_kind',
                'sync_scope_id',
                'namespace',
                'record_id',
                'namespace_revision',
            }, f'purge 载荷混入了被删事实的内容：{event["payload"]}'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 八、回灌不得抹平溯源（§8.2 · 来源节点回灌自己的事件不破坏本地自产片）
# --------------------------------------------------------------------------------------


async def test_downlink_payload_carries_full_origin_and_overlay(session: AsyncSession) -> None:
    """来源节点会按游标把自己刚推的事件拉回去——回灌载荷必须完整带溯源与 overlay 六列。

    ``pull_memory_events`` 只按 ``namespace_revision`` 游标增量、**不排除来源节点**，所以来源
    节点必然回灌到自己的事件。这不构成回环（下行 applier 写镜像、不入 outbox），但若回发的
    载荷缺 ``origin_*``，下行的 ``ON CONFLICT DO UPDATE`` 会把该节点自产片行的归属抹平，
    那条事实从此不可整理（doc19 §8.2 明列为 S5 必查项）。
    """
    ids = _Ids()
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact),
        )
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.merge_verdict',
            payload={
                'fact_id': ids.fact,
                'merge_verdict': 'disputed',
                'merge_verdict_run': 'run_9',
                'merge_judged_revision': 1,
            },
        )
        events = await _downlink_events(session, ids.owner)
        assert [e['event_type'] for e in events] == [
            'memory.agent_self_fact.upserted',
            'memory.agent_self_fact.upserted',
        ], '下行必须用既有的 memory.{subject}_fact.upserted，本地 memory_restore 只认这几个'

        latest = events[-1]['payload']
        assert latest['origin_kind'] == 'node'
        assert latest['origin_node_id'] == ids.node, '来源节点必须能从回灌里认出这是自己的自产片'
        assert latest['origin_agent_id'] == ids.agent
        assert latest['merged_from'] == []
        assert latest['revision'] == 1
        assert latest['merge_verdict'] == 'disputed'
        assert latest['merge_verdict_run'] == 'run_9'
        assert latest['merge_judged_revision'] == 1
        # 同步信封（供 daemon 增量拉取与 fail-closed 校验）
        assert latest['sync_scope_kind'] == 'agent'
        assert latest['sync_scope_id'] == ids.agent
        assert latest['namespace'] == 'agent_facts'
        # object 必须回发**解析后的值**，不是二次转义的串
        assert latest['object_json'] == '喜欢冰美式'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_subject_fact_lands_in_owner_facts_namespace(session: AsyncSession) -> None:
    """owner/peer/world 主体走 ``owner`` 作用域 + ``facts``；agent_self 才是 ``agent_facts``。

    规则与既有下行 ``memory_restore.rs::parse_semantic_fact_payload`` 逐字一致；不一致会让
    回灌事件在客户端 fail-closed 报 namespace 不符，事实静默停在云端。
    """
    ids = _Ids()
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                subject_kind='owner',
                predicate='作息',
                obj='晚睡',
            ),
        )
        assert (
            await _namespace_revision(session, scope_kind='owner', scope_id=ids.owner, namespace='facts') >= 1
        )
        event = (await _downlink_events(session, ids.owner))[-1]
        assert event['event_type'] == 'memory.owner_fact.upserted'
        assert event['payload']['namespace'] == 'facts'
        assert event['payload']['sync_scope_kind'] == 'owner'
        assert event['payload']['agent_id'] is None
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 九、两侧白名单对称性（§8.3-5 · 实施/98 事故的直接成因）
# --------------------------------------------------------------------------------------

_EXPECTED_UPLINK_EVENTS = frozenset(
    {
        'memory.fact.saved',
        'memory.fact.updated',
        'memory.fact.superseded',
        'memory.fact.withdrawn',
        'memory.fact.purged',
        'memory.fact.merge_verdict',
    }
)

#: 本地事件常量所在的 Rust 源文件（相对各仓库根同级目录）。
_LOCAL_AUTHORITY_RS = Path('crates/hasn-memory/src/storage/authority.rs')


#: 取源规则（配对 worktree 优先 + 找不到就 skip）由公共 helper 唯一实现，两个测试文件共用；
#: 复制粘贴过一次就必然只修一处、漏另一处，正是这条规则原本要防的那类漂移。
_find_local_source = find_local_rust_source


def _find_local_authority_source() -> Path | None:
    """本地事件名构造点 ``authority.rs``（同一套配对 worktree 优先规则）。"""
    return _find_local_source(_LOCAL_AUTHORITY_RS)


async def test_uplink_whitelist_is_pinned_to_expected_six_events() -> None:  # noqa: RUF029 模块级 pytestmark 是 asyncio，纯契约钉子无需 await
    """§8.3-5 钉子：云端白名单必须**恰好**是这六个，多一个少一个都要显式改这条测试。"""
    assert MEMORY_FACT_UPLINK_EVENTS == _EXPECTED_UPLINK_EVENTS


async def test_uplink_whitelist_matches_local_rust_constants() -> None:  # noqa: RUF029 模块级 pytestmark 是 asyncio，纯契约钉子无需 await
    """同机能看到 hasn-node 检出时，逐条比对真实 Rust 源文件里的事件名。

    对照点：``crates/hasn-memory/src/storage/authority.rs::MemoryOp::fact_event_type``。
    """
    source = _find_local_authority_source()
    if source is None:
        pytest.skip('本机没有并排的 hasn-node 检出，已由上一条显式期望集合钉子兜底')
    body = source.read_text(encoding='utf-8')
    match = re.search(r'fn fact_event_type\(self\)[^{]*\{(.*?)\n    \}', body, re.DOTALL)
    assert match is not None, f'{source} 里找不到 fact_event_type，两侧对照点已漂移'
    local_events = set(re.findall(r'"(memory\.fact\.[a-z_]+)"', match.group(1)))
    assert local_events == set(MEMORY_FACT_UPLINK_EVENTS), (
        f'两侧上行事件白名单不对称（实施/98 事故的直接成因）：'
        f'本地={sorted(local_events)} 云端={sorted(MEMORY_FACT_UPLINK_EVENTS)}；'
        f'本地源文件={source}'
    )


async def test_error_code_allocation_is_pinned() -> None:  # noqa: RUF029 模块级 pytestmark 是 asyncio，纯契约钉子无需 await
    """错误码分配表钉死；新增码必须显式改这条，避免与既有 80xx 段撞号。

    ⚠️ 8043/8044/8045 还必须登记进客户端的
    ``hasn-node/crates/hasn-node/src/backend/types/sync.rs::SyncRejected::is_permanent()``，
    否则 daemon 会把它们当未知码走退避重试——不会洪水，但永久拒绝的事件不会出队、
    ``purge_local`` 指令也不会被执行。
    """
    assert MEMORY_FACT_UPLINK_ERROR_CODES == {
        'ERR_MEMORY_FACT_PAYLOAD_INVALID': 8043,
        'ERR_MEMORY_FACT_ORIGIN_MISMATCH': 8044,
        'ERR_MEMORY_FACT_PURGED': 8045,
        'ERR_SYNC_EVENT_CONFLICT': 8041,
    }
    assert set(MEMORY_FACT_PERMANENT_CODES) == {8043, 8044, 8045}


# --------------------------------------------------------------------------------------
# 十、跨仓契约钉子（本地 Rust 源文件在场时逐条比对真源）
# --------------------------------------------------------------------------------------

#: 本地上行 drain 所在的 Rust 源文件（事件白名单 + 拒绝分类都在这里）。
_LOCAL_MEMORY_PUSH_RS = Path('crates/hasn-node/src/runtime/memory_push.rs')
#: 本地主人事件标记与快照构造所在的 Rust 源文件（§4.6 / D-20）。
_LOCAL_FACTS_RS = Path('crates/hasn-memory/src/storage/facts.rs')
#: 本地错误码分类所在的 Rust 源文件。
_LOCAL_SYNC_TYPES_RS = Path('crates/hasn-node/src/backend/types/sync.rs')


async def test_uplink_whitelist_matches_local_drain_constant() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """§8.3-5：云端白名单还必须与**本地 drain 的白名单常量**逐条对称。

    上一条测试比的是 ``authority.rs::MemoryOp::fact_event_type``（事件名的构造点）；
    真正决定「哪些事件会被发出去」的是 drain 里的
    ``memory_push.rs::MEMORY_UPLINK_EVENT_TYPES``——两处在本地就可能漂移，
    故两条都要比。
    """
    source = _find_local_source(_LOCAL_MEMORY_PUSH_RS)
    if source is None:
        pytest.skip('本机没有并排的 hasn-node 检出，已由显式期望集合钉子兜底')
    body = source.read_text(encoding='utf-8')
    match = re.search(r'MEMORY_UPLINK_EVENT_TYPES:\s*\[&str;\s*\d+\]\s*=\s*\[(.*?)\];', body, re.DOTALL)
    assert match is not None, f'{source} 里找不到 MEMORY_UPLINK_EVENT_TYPES，两侧对照点已漂移'
    local_events = set(re.findall(r'"(memory\.fact\.[a-z_]+)"', match.group(1)))
    assert local_events == set(MEMORY_FACT_UPLINK_EVENTS), (
        f'两侧上行事件白名单不对称（实施/98 事故的直接成因）：'
        f'本地 drain={sorted(local_events)} 云端={sorted(MEMORY_FACT_UPLINK_EVENTS)}'
    )


async def test_local_daemon_reads_the_purge_local_directive_key_we_actually_send() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """**链路钉子**：本地必须按 ``detail.action`` 读 purge 指令，而不是某个我们从没发过的键。

    云端 ``_purge_local_error()`` 发的是 ``{'action': 'purge_local', 'fact_id': ...}``；
    本地若读 ``detail.purge_local``（布尔旗标），墓碑命中时**永远收不到删除指令**——
    本地行不删、op 一直挂在 outbox 每轮重试到毒丸上限。这条曾经真的不对齐过。
    """
    source = _find_local_source(_LOCAL_MEMORY_PUSH_RS)
    if source is None:
        pytest.skip('本机没有并排的 hasn-node 检出')
    body = source.read_text(encoding='utf-8')
    match = re.search(r'fn purge_local_requested\(.*?\n\}', body, re.DOTALL)
    assert match is not None, f'{source} 里找不到 purge_local_requested，两侧对照点已漂移'
    reader = match.group(0)
    assert '"action"' in reader, f'本地必须按 detail.action 读 purge 指令：\n{reader}'
    assert 'get("purge_local")' not in reader, (
        f'云端从未发过 detail.purge_local 布尔旗标，读它等于永远收不到指令：\n{reader}'
    )
    # 取值可以写字面量，也可以走本地常量（本地现用 `PURGE_LOCAL_ACTION`）——两种都认，
    # 但取值本身必须逐字是 `purge_local`。
    action_const = re.search(r'PURGE_LOCAL_ACTION:\s*&str\s*=\s*"([^"]+)"', body)
    action_value = action_const.group(1) if action_const else None
    assert '"purge_local"' in reader or action_value == 'purge_local', (
        f'指令取值必须逐字是 purge_local（本地常量取值={action_value!r}）：\n{reader}'
    )


async def test_local_permanent_code_set_matches_cloud() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """§8.3-4：本地 ``SyncRejected::is_permanent()`` 必须覆盖云端全部永久码、且**不含冲突码**。

    漏码 → 永久拒绝的事件不出队、``purge_local`` 不执行；
    多收冲突码（8041/8042）→ 一次乱序就把一条记忆永久丢掉。两个方向都要钉。
    """
    source = _find_local_source(_LOCAL_SYNC_TYPES_RS)
    if source is None:
        pytest.skip('本机没有并排的 hasn-node 检出')
    body = source.read_text(encoding='utf-8')
    match = re.search(r'fn is_permanent\(&self\)\s*->\s*bool\s*\{(.*?)\n    \}', body, re.DOTALL)
    assert match is not None, f'{source} 里找不到 is_permanent，两侧对照点已漂移'
    local_codes = {int(code) for code in re.findall(r'\b(80\d\d)\b', match.group(1))}
    missing = set(MEMORY_FACT_PERMANENT_CODES) - local_codes
    assert not missing, f'本地 is_permanent 漏了云端永久码 {sorted(missing)}：永久拒绝的事件将永不出队'
    leaked = local_codes & {8041, 8042}
    assert not leaked, f'冲突码 {sorted(leaked)} 混进了本地永久集合：一次乱序就会永久丢一条记忆'


# --------------------------------------------------------------------------------------
# 十一、supersedes_hint 双端往返（doc19 §4.3 / §8.2 增列汇总）
# --------------------------------------------------------------------------------------


async def test_supersedes_hint_lands_from_uplink_and_is_echoed_downlink(session: AsyncSession) -> None:
    """§4.3 / §8.2：本人纠正指向是**双端列**——上行落列、整理可改、回灌带回。

    这条列曾经只在云端存在：本地把 hint 编码进 ``source_refs``、云端 apply 又完全不读它，
    结果是 ``save(supersedes_hint=...)`` 写下的线索**永远到不了云端**，S6 合并规则层的
    「本人纠正本人直接裁决」等于从来不会触发。
    """
    ids = _Ids()
    old_fact = uuid.uuid4().hex
    try:
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.saved',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                supersedes_hint=old_fact,
            ),
        )
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['supersedes_hint'] == old_fact, 'saved 必须把 hint 落到正式列'

        # 回灌必须把它带回来，否则来源节点自己拉回这条事件时会把本地 hint 抹平。
        events = await _downlink_events(session, ids.owner)
        assert events, '应有一条回灌事件'
        assert events[-1]['payload']['supersedes_hint'] == old_fact

        # 整理属业务字段组，hint 随之覆盖（这里清空）。
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.updated',
            payload=_snapshot(
                owner_id=ids.owner,
                node_id=ids.node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=2,
                supersedes_hint=None,
            ),
        )
        cleared = await _row(session, ids.fact)
        assert cleared is not None
        assert cleared['supersedes_hint'] is None
        assert cleared['revision'] == 2
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_supersedes_hint_over_column_width_is_permanently_rejected(session: AsyncSession) -> None:
    """宽度必须与本列 ``varchar(40)`` 一致（本地 crate 的 fact_id 同宽）。

    超长在解析层就按 4xx 语义永久拒绝，绝不让它撞进 DB 变成 5xx / error 洪水。
    """
    ids = _Ids()
    try:
        with pytest.raises(FactUplinkPermanentError) as excinfo:
            await fact_uplink_service.apply_fact_event(
                session,
                owner_id=ids.owner,
                node_id=ids.node,
                event_type='memory.fact.saved',
                payload=_snapshot(
                    owner_id=ids.owner,
                    node_id=ids.node,
                    agent_id=ids.agent,
                    fact_id=ids.fact,
                    supersedes_hint='x' * 41,
                ),
            )
        assert excinfo.value.error.code == 8043
        assert 'supersedes_hint' in excinfo.value.reason
    finally:
        await _cleanup(session, ids.owner, ids.agent)


# --------------------------------------------------------------------------------------
# 十一、主人第三类写者（§4.6 / D-20）——`owner_write` 标记豁免 origin 守卫
# --------------------------------------------------------------------------------------
#
# 这一组守的是一个**永久性**缺陷：没有豁免时，主人从设备 X 改一条 origin=设备 Y 的事实会吃
# 8044 永久拒绝——本地已经生效（记忆页立即可见、disputed 立即失效），却永远传播不到其余节点，
# 跨设备记忆就此分叉，而且分叉是静默的（daemon 丢弃 op + 一次性 warn，UI 上什么都看不到）。


def _owner_write(payload: dict[str, Any]) -> dict[str, Any]:
    """给一份事实快照加上主人事件标记（本地 ``owner_fact_snapshot_json`` 的等价构造）。"""
    return {**payload, OWNER_WRITE_MARKER: True}


async def _seed_from(session: AsyncSession, ids: _Ids, *, node_id: str) -> None:
    """在 ``node_id`` 上落一条自产片事实（后续用另一个节点冒充「主人的另一台设备」）。"""
    await _apply(
        session,
        owner_id=ids.owner,
        node_id=node_id,
        event_type='memory.fact.saved',
        payload=_snapshot(owner_id=ids.owner, node_id=node_id, agent_id=ids.agent, fact_id=ids.fact),
    )


async def test_owner_write_marker_key_matches_local_rust_constant() -> None:  # noqa: RUF029 纯契约钉子无需 await
    """标记键名必须与本地 ``facts.rs::OWNER_WRITE_MARKER`` 逐字一致。

    键名单边改掉不会报任何错——云端只会把主人事件当分身整理，8044 永久拒绝照旧，
    症状与「压根没实现豁免」一模一样。
    """
    source = find_local_rust_source(_LOCAL_FACTS_RS)
    if source is None:
        pytest.skip('本机没有并排的 hasn-node 检出，已由本模块常量的显式取值兜底')
    body = source.read_text(encoding='utf-8')
    match = re.search(r'pub const OWNER_WRITE_MARKER:\s*&str\s*=\s*"([a-z_]+)"', body)
    assert match is not None, f'{source} 里找不到 OWNER_WRITE_MARKER，两侧对照点已漂移'
    assert match.group(1) == OWNER_WRITE_MARKER, (
        f'主人事件标记键两侧不一致：本地={match.group(1)} 云端={OWNER_WRITE_MARKER}；本地源文件={source}'
    )


async def test_owner_write_can_edit_another_nodes_fact(session: AsyncSession) -> None:
    """§4.6 / D-20 核心：主人从设备 B 改一条 origin=设备 A 的事实必须成立。

    同时钉住溯源不被改写——归属若随一次主人写易主，「谁有权整理它」就悄悄变了（§3.2 / §4.2）。
    """
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)

        out = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,  # 主人此刻坐在**另一台设备**前
            event_type='memory.fact.updated',
            payload=_owner_write(
                _snapshot(
                    owner_id=ids.owner,
                    node_id=ids.other_node,
                    agent_id=ids.agent,
                    fact_id=ids.fact,
                    revision=2,
                    obj='改成了手冲',
                    origin_node_id=ids.node,  # 快照仍如实带原 origin（本地不改溯源）
                    confidence=0.95,
                )
            ),
        )
        assert out['outcome'] == 'applied'

        row = await _row(session, ids.fact)
        assert row is not None
        assert row['revision'] == 2, '主人写必须让 revision 前进'
        assert row['object_json'] == '"改成了手冲"'
        assert float(row['confidence']) == pytest.approx(0.95)
        # 溯源四列不随主人写易主（`_apply_business` 的 DO UPDATE 故意不含它们）
        assert row['origin_kind'] == 'node'
        assert row['origin_node_id'] == ids.node
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_invalidates_existing_merge_overlay(session: AsyncSession) -> None:
    """§3.4 / §4.6：主人确认使 revision 前进 → 既有 overlay 因 judged_revision 不匹配自动失效。

    「主人确认一条 disputed 事实后**立即**恢复可见，无需等下轮合并」的落点就在这里。overlay
    三列本身**不被清空**（那是合并的字段组），失效靠 ``merge_judged_revision IS DISTINCT FROM
    revision`` 判定。
    """
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)
        # 主脑把它裁成 disputed（依据 revision=1）
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.merge_verdict',
            payload={
                'fact_id': ids.fact,
                'merge_verdict': 'disputed',
                'merge_verdict_run': 'run_disputed',
                'merge_judged_revision': 1,
            },
        )
        before = await _row(session, ids.fact)
        assert before is not None
        assert before['merge_judged_revision'] == before['revision'], '裁决此刻是生效的'

        # 主人在另一台设备上确认（提档到 0.95），revision 前进
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.updated',
            payload=_owner_write(
                _snapshot(
                    owner_id=ids.owner,
                    node_id=ids.other_node,
                    agent_id=ids.agent,
                    fact_id=ids.fact,
                    revision=2,
                    origin_node_id=ids.node,
                    confidence=0.95,
                )
            ),
        )
        after = await _row(session, ids.fact)
        assert after is not None
        assert after['revision'] == 2
        assert after['merge_verdict'] == 'disputed', 'overlay 三列由合并维护，主人写不碰它们'
        assert after['merge_judged_revision'] == 1
        assert after['merge_judged_revision'] != after['revision'], '裁决已自动失效 → 事实恢复可见'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_agent_write_on_foreign_node_still_rejected_marker_is_the_only_difference(
    session: AsyncSession,
) -> None:
    """同一份载荷：不带标记 → 8044 永久拒；带标记 → 接受。判据只有这一个标记。

    §8.3-3 的自产片判据对**分身整理**一寸没松：分身伪造不了主人身份也就改不动他节点事实。
    """
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)
        payload = _snapshot(
            owner_id=ids.owner,
            node_id=ids.other_node,
            agent_id=ids.agent,
            fact_id=ids.fact,
            revision=2,
            status='withdrawn',
            origin_node_id=ids.node,
        )

        agent_decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.withdrawn',
            payload=payload,
        )
        assert agent_decision.outcome == 'reject_permanent'
        assert agent_decision.error is not None
        assert agent_decision.error.code == 8044

        owner_decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.withdrawn',
            payload=_owner_write(payload),
        )
        assert owner_decision.outcome == 'apply'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_marker_must_be_boolean_true(session: AsyncSession) -> None:
    """标记只认布尔 ``true``：``'true'`` / ``1`` / ``None`` 一律不算，照旧走分身判据。

    宽松解析换不来兼容性（本地写的就是 JSON ``true``），只会给伪造多留活口。
    """
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)
        base = _snapshot(
            owner_id=ids.owner,
            node_id=ids.other_node,
            agent_id=ids.agent,
            fact_id=ids.fact,
            revision=2,
            origin_node_id=ids.node,
        )
        for bogus in ('true', 1, 'yes', None):
            decision = await fact_uplink_service.classify(
                session,
                owner_id=ids.owner,
                node_id=ids.other_node,
                event_type='memory.fact.updated',
                payload={**base, OWNER_WRITE_MARKER: bogus},
            )
            assert decision.outcome == 'reject_permanent', f'{bogus!r} 不该被当成主人事件'
            assert decision.error is not None
            assert decision.error.code == 8044
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_cannot_touch_another_owners_fact(session: AsyncSession) -> None:
    """owner 边界**不随节点边界一起松**：带标记也只能改自己名下的事实。

    走真实链路时越权者连行都读不到（``_read_fact`` 按 owner 过滤），故表现为可退避冲突而不是
    落库；行必须分毫未动。
    """
    ids = _Ids()
    victim = _Ids()
    try:
        await _seed_from(session, victim, node_id=victim.node)
        before = await _row(session, victim.fact)
        assert before is not None

        with pytest.raises(FactUplinkConflictError):
            await fact_uplink_service.apply_fact_event(
                session,
                owner_id=ids.owner,  # 攻击者自己的 owner 凭据（认证信封给的，改不了）
                node_id=ids.node,
                event_type='memory.fact.withdrawn',
                payload=_owner_write(
                    _snapshot(
                        owner_id=ids.owner,
                        node_id=ids.node,
                        agent_id=ids.agent,
                        fact_id=victim.fact,
                        revision=99,
                        status='withdrawn',
                    )
                ),
            )
        after = await _row(session, victim.fact)
        assert after is not None
        assert after['status'] == before['status']
        assert after['revision'] == before['revision']
        assert after['owner_id'] == victim.owner
    finally:
        await _cleanup(session, victim.owner, victim.agent)
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_owner_guard_is_structurally_redundant() -> None:  # noqa: RUF029 纯守卫断言不碰库
    """结构性冗余断言：即便读侧漏掉 owner 过滤，主人写也必须在这里当场炸。

    直接喂一行 owner 不符的 ``current``——将来谁把 ``_read_fact`` 改成不带 owner 过滤，
    这条会立刻变红，而不是悄悄放行一次跨主人写。
    """
    ids = _Ids()
    foreign_row: dict[str, Any] = {
        'fact_id': ids.fact,
        'owner_id': f'h_other{uuid.uuid4().hex[:14]}',
        'origin_kind': 'node',
        'origin_node_id': ids.node,
        'revision': 1,
    }
    parsed = _parse_fact(
        _snapshot(owner_id=ids.owner, node_id=ids.node, agent_id=ids.agent, fact_id=ids.fact, revision=2),
        owner_id=ids.owner,
    )
    with pytest.raises(FactUplinkPermanentError) as excinfo:
        # 私有守卫本身就是被测对象：这条断言的价值正在于「绕过读侧也炸」。
        fact_uplink_service._assert_editable_own_slice(
            foreign_row, parsed, node_id=ids.node, owner_id=ids.owner, owner_write=True
        )
    assert excinfo.value.error.code == 8044
    assert '本人' in excinfo.value.reason


async def test_owner_write_is_still_blocked_by_tombstone(session: AsyncSession) -> None:
    """§8.3-6 墓碑防复活对主人事件同样适用：改一条已被硬删的事实是无意义的复活。"""
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)
        await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.node,
            event_type='memory.fact.purged',
            payload={'fact_id': ids.fact, 'owner_id': ids.owner, 'purged_by': ids.owner},
        )
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.updated',
            payload=_owner_write(
                _snapshot(
                    owner_id=ids.owner,
                    node_id=ids.other_node,
                    agent_id=ids.agent,
                    fact_id=ids.fact,
                    revision=2,
                    origin_node_id=ids.node,
                )
            ),
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8045
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_is_still_subject_to_revision_monotonic_guard(session: AsyncSession) -> None:
    """幂等键仍是 ``(owner, fact_id, revision)``：主人事件绕过 origin 判据，绝不绕过重放判据。"""
    ids = _Ids()
    try:
        await _seed_from(session, ids, node_id=ids.node)
        payload = _owner_write(
            _snapshot(
                owner_id=ids.owner,
                node_id=ids.other_node,
                agent_id=ids.agent,
                fact_id=ids.fact,
                revision=2,
                obj='第一次改',
                origin_node_id=ids.node,
            )
        )
        first = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.updated',
            payload=payload,
        )
        assert first['outcome'] == 'applied'
        revision_after_first = await _namespace_revision(
            session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts'
        )

        replay = await _apply(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.updated',
            payload={**payload, 'object_json': '重推时被人改过的内容'},
        )
        assert replay['outcome'] == 'replay'
        row = await _row(session, ids.fact)
        assert row is not None
        assert row['object_json'] == '"第一次改"', '重放不得改库'
        assert (
            await _namespace_revision(session, scope_kind='agent', scope_id=ids.agent, namespace='agent_facts')
            == revision_after_first
        ), '重放不得推进游标（否则给各节点造无意义回灌）'
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_marker_does_not_relax_saved_origin_guard(session: AsyncSession) -> None:
    """``saved`` 不认豁免：§4.6 放开的是「整理」，不是「替别人铸造自产片」。

    放开这里等于允许一个节点替另一个节点新建自产片，溯源从第一天就是假的。
    """
    ids = _Ids()
    try:
        decision = await fact_uplink_service.classify(
            session,
            owner_id=ids.owner,
            node_id=ids.other_node,
            event_type='memory.fact.saved',
            payload=_owner_write(
                _snapshot(
                    owner_id=ids.owner,
                    node_id=ids.other_node,
                    agent_id=ids.agent,
                    fact_id=ids.fact,
                    origin_node_id=ids.node,
                )
            ),
        )
        assert decision.outcome == 'reject_permanent'
        assert decision.error is not None
        assert decision.error.code == 8044
    finally:
        await _cleanup(session, ids.owner, ids.agent)


async def test_owner_write_on_missing_fact_is_retryable_conflict(session: AsyncSession) -> None:
    """事实还没汇聚时的主人写是**冲突**（可退避），不是永久拒绝——判成永久就是永久丢一次编辑。"""
    ids = _Ids()
    try:
        with pytest.raises(FactUplinkConflictError) as excinfo:
            await fact_uplink_service.apply_fact_event(
                session,
                owner_id=ids.owner,
                node_id=ids.other_node,
                event_type='memory.fact.updated',
                payload=_owner_write(
                    _snapshot(
                        owner_id=ids.owner,
                        node_id=ids.other_node,
                        agent_id=ids.agent,
                        fact_id=ids.fact,
                        revision=2,
                        origin_node_id=ids.node,
                    )
                ),
            )
        assert excinfo.value.error.code == 8041
        assert excinfo.value.error.code not in MEMORY_FACT_PERMANENT_CODES
    finally:
        await _cleanup(session, ids.owner, ids.agent)
