"""SyncAppender contract suite（R1-04·真实 PG·零 mock）。

验证 `SqlAlchemySyncAppender`（薄封装现网 `_append_sync_event_with_id` chokepoint）满足
`SyncAppender` port 契约、且**行为与现网一致**：

1. append 在调用方事务内落库，返回良构 `SyncEventRef`（revision≥1、event_id 非空）；
2. 同一 owner 连续 append → revision 单调 +1（现网 advisory-lock 分配语义经 port 忠实透出）；
3. 并发 append（多连接 gather）→ revision 无空洞无冲突（复用 cloud 8a125cdf 回归法，
   钉死 per-owner `pg_advisory_xact_lock` 串行化在 port 之下依然生效）；
4. append 与业务写同一 Session/事务——回滚一并回滚（§7.1 事务收口的最小证据）。

需要本地 PG（export DATABASE_PORT=15432）；不可达则跳过。每用例用 uuid 派生全新 owner、
末尾清理自身行，不污染库。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_sync.adapters.sqlalchemy_appender import SqlAlchemySyncAppender
from backend.app.hasn_sync.ports.dto import SyncEnvelope
from backend.app.hasn_sync.ports.sync_appender import SyncAppender
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 SyncAppender 契约套件：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fresh_owner() -> str:
    return f'h_sa{uuid.uuid4().hex[:18]}'


def _envelope(owner: str, n: int, *, producer: str | None = None, source_event_id: str | None = None) -> SyncEnvelope:
    return SyncEnvelope(
        owner_id=owner,
        hasn_id=owner,
        event_type='contract.probe',
        aggregate_type='probe',
        aggregate_id=f'p_{n}',
        payload={'n': n, 'note': '契约探针'},
        producer=producer,
        source_event_id=source_event_id,
    )


async def _count_rows(sessionmaker, owner: str) -> int:
    async with sessionmaker() as session:
        return (
            await session.execute(
                sa.text('SELECT count(*) FROM public.hasn_sync_events WHERE owner_id = :o'),
                {'o': owner},
            )
        ).scalar()


async def _cleanup(sessionmaker, owner: str) -> None:
    async with sessionmaker() as session:
        await session.execute(
            sa.text('DELETE FROM public.hasn_sync_events WHERE owner_id = :o'),
            {'o': owner},
        )
        await session.commit()


async def test_append_returns_wellformed_ref(sessionmaker_pg):
    # 结构化子类型检查：adapter 实现 SyncAppender 契约（append 方法齐）
    assert isinstance(SqlAlchemySyncAppender(), SyncAppender)
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    try:
        async with sessionmaker_pg() as db:
            ref = await appender.append(db, _envelope(owner, 1))
            await db.commit()
        assert ref.owner_id == owner
        assert ref.revision >= 1
        assert ref.event_id and ref.event_id.startswith('se_')
        assert ref.event_type == 'contract.probe'
    finally:
        await _cleanup(sessionmaker_pg, owner)


async def test_sequential_appends_monotonic_revision(sessionmaker_pg):
    """同 owner 连续 append → revision 严格 +1（首个 owner 从 1 起）。"""
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    try:
        revisions: list[int] = []
        for n in range(1, 4):
            async with sessionmaker_pg() as db:
                ref = await appender.append(db, _envelope(owner, n))
                await db.commit()
                revisions.append(ref.revision)
        assert revisions == [1, 2, 3]
    finally:
        await _cleanup(sessionmaker_pg, owner)


async def test_concurrent_appends_no_gap_no_conflict(sessionmaker_pg):
    """并发 append（10 条各自连接 gather）→ revision = {1..10} 无空洞无重复。

    复用 cloud 8a125cdf 回归法：per-owner pg_advisory_xact_lock 把 MAX+1 分配串行化，
    这条不变量在 port 封装之下必须依旧成立。
    """
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    n = 10
    try:
        async def one(idx: int) -> int:
            async with sessionmaker_pg() as db:
                ref = await appender.append(db, _envelope(owner, idx))
                await db.commit()
                return ref.revision

        got = sorted(await asyncio.gather(*[one(i) for i in range(1, n + 1)]))
        assert got == list(range(1, n + 1)), f'并发 revision 出现空洞/冲突：{got}'
    finally:
        await _cleanup(sessionmaker_pg, owner)


async def test_append_dedups_same_producer_source(sessionmaker_pg):
    """R2-07 幂等：同 (owner, producer, source_event_id) 二次 append → 返回原 revision、deduped=True、不新增行。"""
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    src = f'evt_{uuid.uuid4().hex[:16]}'
    try:
        async with sessionmaker_pg() as db:
            first = await appender.append(db, _envelope(owner, 1, producer='hasn_im', source_event_id=src))
            await db.commit()
        assert first.deduped is False
        assert first.revision >= 1

        async with sessionmaker_pg() as db:
            second = await appender.append(db, _envelope(owner, 2, producer='hasn_im', source_event_id=src))
            await db.commit()
        # 命中已落行：返回同一 revision/event_id，标记 deduped，且库里仍只有 1 行。
        assert second.deduped is True
        assert second.revision == first.revision
        assert second.event_id == first.event_id
        assert await _count_rows(sessionmaker_pg, owner) == 1
    finally:
        await _cleanup(sessionmaker_pg, owner)


async def test_append_source_event_id_owner_scoped(sessionmaker_pg):
    """R2-07 去重键含 owner：同一 source_event_id 扇出到两个 owner → 各落一行、均非 deduped、各自 revision=1。

    这正是 sync_projector 把一条集成事件扇出到多受众的核心不变量——绝不能被误去重成只写头一个 owner。
    """
    owner_a = _fresh_owner()
    owner_b = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    src = f'evt_{uuid.uuid4().hex[:16]}'
    try:
        async with sessionmaker_pg() as db:
            ref_a = await appender.append(db, _envelope(owner_a, 1, producer='hasn_im', source_event_id=src))
            ref_b = await appender.append(db, _envelope(owner_b, 1, producer='hasn_im', source_event_id=src))
            await db.commit()
        assert ref_a.deduped is False and ref_a.revision == 1
        assert ref_b.deduped is False and ref_b.revision == 1
        assert await _count_rows(sessionmaker_pg, owner_a) == 1
        assert await _count_rows(sessionmaker_pg, owner_b) == 1
    finally:
        await _cleanup(sessionmaker_pg, owner_a)
        await _cleanup(sessionmaker_pg, owner_b)


async def test_append_producer_source_both_or_neither(sessionmaker_pg):
    """R2-07 校验：只给 producer 不给 source_event_id（反之亦然）→ 函数抛错（去重键残缺不允许）。"""
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    try:
        with pytest.raises(Exception) as exc_info:
            async with sessionmaker_pg() as db:
                await appender.append(db, _envelope(owner, 1, producer='hasn_im', source_event_id=None))
                await db.commit()
        # 函数以 check_violation 抛出，错误信息含中文校验语
        assert 'source_event_id' in str(exc_info.value)
        assert await _count_rows(sessionmaker_pg, owner) == 0
    finally:
        await _cleanup(sessionmaker_pg, owner)


async def test_append_shares_caller_transaction_rollback(sessionmaker_pg):
    """append 落在调用方事务内——事务回滚则该事件一并消失（§7.1 同事务证据）。"""
    owner = _fresh_owner()
    appender = SqlAlchemySyncAppender()
    try:
        async with sessionmaker_pg() as db:
            await appender.append(db, _envelope(owner, 1))
            # 不 commit，直接回滚——模拟业务写失败连累同步事件一并撤销
            await db.rollback()

        async with sessionmaker_pg() as db:
            count = (
                await db.execute(
                    sa.text('SELECT count(*) FROM public.hasn_sync_events WHERE owner_id = :o'),
                    {'o': owner},
                )
            ).scalar()
        assert count == 0, '回滚后不应有残留事件（append 未与调用方事务绑定）'
    finally:
        await _cleanup(sessionmaker_pg, owner)
