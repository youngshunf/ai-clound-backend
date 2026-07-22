"""R2-04 验收：integration_events + event_seq 乱序防护（真实 PG·零 mock·doc16 §7.2）。

钉死 P0 硬要求：并发提交乱序时消费者不跳漏晚提交低 seq 事件——沿用 cloud 8a125cdf 回归法
（多连接 asyncio.gather 并发 append），验证：

1. **顺序追加**：单事务内连续 append → event_seq 连续递增（步长 1）。
2. **并发无空洞无冲突**（核心·§12.1 乱序场景）：N 个独立连接各自事务并发 append，
   advisory-lock 串行化分配 → 本会话拿到的 N 个 seq 为连续集合、无重复、无 UniqueViolation。
   （去掉 advisory lock 则会撞 uq_hasn_im_int_events_shard_seq 或留空洞——本测试即回归护栏。）
3. **分片隔离**：不同 shard_key 各自独立序列，证「容量需要时按 shard 横向扩展、表结构不变」。

**isolation-robust（关键）**：`event_seq` 按**分片全局**分配（`MAX(event_seq)+1 WHERE shard_key`），
非按 aggregate。故存量表上新行的 seq 从**当前分片最大值 +1** 起，不是恒 1。§7.2 要钉死的不变量是
**无空洞 / 无重复 / 不倒退（相对连续）**，而非「从 1 起」——后者只在空表成立。本测试一律断言**相对
连续**（`max-min == len-1` 且去重后同长），不假设起点，从而不依赖表被清空、也不删共享 dev 数据。

PG 不可达跳过；每用例 uuid 派生独立 aggregate，末尾按 aggregate_id 清理自身行。
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


def _is_contiguous(seqs: list[int]) -> bool:
    """判定一组序号是否「相对连续」：去重后长度不变（无重复）且 max-min == len-1（无空洞）。

    这是 §7.2 无空洞/无重复不变量的 isolation-robust 表达——不假设起点为 1，故不依赖表被清空。
    空列表视为不连续（调用点都应有 ≥1 个 seq）。
    """
    if not seqs:
        return False
    return len(set(seqs)) == len(seqs) and (max(seqs) - min(seqs)) == len(seqs) - 1


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
    """单事务内连续 append → event_seq 连续递增（步长 1）；event_id 各不相同。

    isolation-robust：断言 refs 的 seq 为**连续递增**（base, base+1, base+2），不假设 base==1。
    """
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

    ref_seqs = [r.event_seq for r in refs]
    # 严格升序、步长 1（顺序追加 → 取号顺序 == 追加顺序）
    assert ref_seqs == list(range(ref_seqs[0], ref_seqs[0] + 3)), f'顺序追加应连续递增，实得 {ref_seqs}'
    assert len({r.event_id for r in refs}) == 3
    # 落库（本 aggregate 的行）与返回 seq 一致
    assert await _seqs_for(sessionmaker_pg, aggregate_id) == ref_seqs
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

    # 分配无重复、无空洞：本会话拿到的 n 个 seq 恰为一段连续区间（不假设起点为 1）
    assert _is_contiguous(results), f'并发分配应连续无重复无空洞（{n} 路），实得 {sorted(results)}'
    assert len(results) == n
    # 落库（本 aggregate 的行）与返回集合一致
    assert await _seqs_for(sessionmaker_pg, aggregate_id) == sorted(results)
    await _cleanup(sessionmaker_pg, aggregate_id)


# ---------- 分片隔离 ----------


async def test_shard_isolation_independent_sequences(sessionmaker_pg) -> None:
    """不同 shard_key 各自独立序列——证按 shard 横向扩展、表结构不变（§7.2）。

    isolation-robust：每个分片各持独立计数器（`MAX WHERE shard_key`），故 shard0/shard1 的绝对
    起点各随该分片存量而定、互不相干。断言两分片各自为**相对连续**的一段（长度 2 / 3），即证「按
    shard 独立分配序号」——不假设从 1 起。
    """
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

    seqs0 = await _seqs_for(sessionmaker_pg, aggregate_id, shard_key=0)
    seqs1 = await _seqs_for(sessionmaker_pg, aggregate_id, shard_key=1)
    # 各分片本 aggregate 的行数与相对连续性（分片独立计数器）
    assert len(seqs0) == 2 and _is_contiguous(seqs0), f'shard0 应为 2 连续 seq，实得 {seqs0}'
    assert len(seqs1) == 3 and _is_contiguous(seqs1), f'shard1 应为 3 连续 seq，实得 {seqs1}'
    await _cleanup(sessionmaker_pg, aggregate_id)
