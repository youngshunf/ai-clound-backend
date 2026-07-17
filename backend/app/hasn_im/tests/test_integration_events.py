"""R2-04 验收：integration_events + event_seq 乱序防护（真实 PG·零 mock·doc16 §7.2）。

钉死 P0 硬要求：并发提交乱序时消费者不跳漏晚提交低 seq 事件——沿用 cloud 8a125cdf 回归法
（多连接 asyncio.gather 并发 append），验证：

1. **顺序追加**：单事务内连续 append → event_seq 1,2,3 连续。
2. **并发无空洞无冲突**（核心·§12.1 乱序场景）：N 个独立连接各自事务并发 append，
   advisory-lock 串行化分配 → event_seq 恰为 {1..N} 连续集合、无重复、无 UniqueViolation。
   （去掉 advisory lock 则会撞 uq_hasn_im_int_events_shard_seq 或留空洞——本测试即回归护栏。）
3. **分片隔离**：不同 shard_key 各自独立序列（均从 1 起），证「容量需要时按 shard 横向扩展、
   表结构不变」。

PG 不可达跳过；每用例 uuid 派生独立 aggregate，末尾按 event_id 清理自身行。
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

from backend.app.hasn_im.application import event_appender as appender
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_TABLE = 'public.hasn_im_integration_events'


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 R2-04 事件日志测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _cleanup(sessionmaker, aggregate_id: str) -> None:
    async with sessionmaker() as session:
        await session.execute(
            sa.text(f'DELETE FROM {_TABLE} WHERE aggregate_id = :aid'),  # noqa: S608 常量表名
            {'aid': aggregate_id},
        )
        await session.commit()


async def _seqs_for(sessionmaker, aggregate_id: str, *, shard_key: int = 0) -> list[int]:
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                sa.text(
                    f'SELECT event_seq FROM {_TABLE} '  # noqa: S608 常量表名
                    'WHERE aggregate_id = :aid AND shard_key = :sk ORDER BY event_seq'
                ),
                {'aid': aggregate_id, 'sk': shard_key},
            )
        ).scalars().all()
    return [int(s) for s in rows]


# ---------- 顺序追加 ----------


async def test_sequential_append_contiguous_seq(sessionmaker_pg) -> None:
    """单事务内连续 append → event_seq 连续 1,2,3；event_id 各不相同。"""
    aggregate_id = f'conv_{uuid.uuid4().hex[:16]}'
    async with sessionmaker_pg() as session:
        refs = []
        for i in range(3):
            ref = await appender.append_event(
                session,
                event_type='im.message.committed.v1',
                aggregate_type='conversation',
                aggregate_id=aggregate_id,
                payload={'i': i},
                aggregate_seq=i + 1,
            )
            refs.append(ref)
        await session.commit()

    assert [r.event_seq for r in refs] == [1, 2, 3]
    assert len({r.event_id for r in refs}) == 3
    assert await _seqs_for(sessionmaker_pg, aggregate_id) == [1, 2, 3]
    await _cleanup(sessionmaker_pg, aggregate_id)


# ---------- 并发无空洞无冲突（核心乱序防护） ----------


async def test_concurrent_append_no_gap_no_conflict(sessionmaker_pg) -> None:
    """N 个独立连接并发 append（8a125cdf 回归法）：advisory-lock 串行分配 → seq 恰为 {1..N}。

    这是 §7.2 P0 乱序防护的护栏：若移除 event_appender 里的 pg_advisory_xact_lock，两个并发
    事务会都读到同一 MAX、算出同一 event_seq、撞 uq_hasn_im_int_events_shard_seq（或留空洞被
    水位跳漏）。此处并发 12 路，断言结果集恰为连续无重复的 {1..12}。
    """
    aggregate_id = f'conv_{uuid.uuid4().hex[:16]}'
    n = 12

    async def _one(idx: int) -> int:
        # 每个协程独立 session（NullPool → 独立连接、独立事务），模拟并发提交乱序。
        async with sessionmaker_pg() as session:
            ref = await appender.append_event(
                session,
                event_type='im.message.committed.v1',
                aggregate_type='conversation',
                aggregate_id=aggregate_id,
                payload={'idx': idx},
            )
            await session.commit()
            return ref.event_seq

    results = await asyncio.gather(*[_one(i) for i in range(n)])

    # 分配无重复、无空洞：结果集恰为 {1..n}
    assert sorted(results) == list(range(1, n + 1)), f'并发分配应为 1..{n} 无重复无空洞，实得 {sorted(results)}'
    # 落库也一致（DB 侧同样连续）
    assert await _seqs_for(sessionmaker_pg, aggregate_id) == list(range(1, n + 1))
    await _cleanup(sessionmaker_pg, aggregate_id)


# ---------- 分片隔离 ----------


async def test_shard_isolation_independent_sequences(sessionmaker_pg) -> None:
    """不同 shard_key 各自独立序列（均从 1 起）——证按 shard 横向扩展、表结构不变（§7.2）。"""
    aggregate_id = f'conv_{uuid.uuid4().hex[:16]}'
    async with sessionmaker_pg() as session:
        for _ in range(2):
            await appender.append_event(
                session,
                event_type='im.message.committed.v1',
                aggregate_type='conversation',
                aggregate_id=aggregate_id,
                payload={},
                shard_key=0,
            )
        for _ in range(3):
            await appender.append_event(
                session,
                event_type='im.message.committed.v1',
                aggregate_type='conversation',
                aggregate_id=aggregate_id,
                payload={},
                shard_key=1,
            )
        await session.commit()

    assert await _seqs_for(sessionmaker_pg, aggregate_id, shard_key=0) == [1, 2]
    assert await _seqs_for(sessionmaker_pg, aggregate_id, shard_key=1) == [1, 2, 3]
    await _cleanup(sessionmaker_pg, aggregate_id)
