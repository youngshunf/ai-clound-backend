"""hasn_sync_events revision 并发分配回归（真实 PG，零 mock）。

复现并防回归：同一 owner 的两个并发写（如同一 owner 的两个 memory 抽取任务 push）在
``SELECT MAX(revision)+1`` 都读到同一个 N、都算出 N+1、都 INSERT 时，会撞
``uq_hasn_sync_events_owner_revision (owner_id, revision)`` 唯一约束抛 UniqueViolationError
（线上现象：``memory.extract_job.upserted`` 落 feed 报 duplicate key revision=238）。

修复 = ``_append_sync_event_with_id`` 起手取事务级 advisory lock，按 owner 串行化 revision
分配——后到的事务阻塞到前一个提交后再读 MAX，拿到正确的下一个值。本测试用 N 个并发
session 同时对同一全新 owner append，断言：全部成功、revision 互不重复、恰好等于 {1..N}。
修复前并发会读到同一 MAX → 撞唯一约束 → 本测试失败。

需要 export DATABASE_PORT=15432（指向本地开发 PG）；PG 不可达时跳过而非硬失败。
末尾清理本测试 owner 的行，不污染库。
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

from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 并发度：足够大以稳定逼出 "读同一 MAX" 的竞态（修复前），同时保持测试轻量。
_CONCURRENCY = 12


@pytest_asyncio.fixture
async def sessionmaker_pg():
    # NullPool：每个 session 独占一条真实连接，async.gather 才能产生真正并发的事务。
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:  # 本地未起开发 PG → 跳过而非硬失败
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过并发回归：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _append_one(sessionmaker: async_sessionmaker, owner_id: str, idx: int) -> int:
    """在独立 session/事务里 append 一条事件并提交，返回分配到的 revision。

    aggregate_id 各不相同 → 是 N 条不同的真实事件（非幂等去重），每条都要拿到唯一 revision。
    """
    gw = SqlAlchemySyncGateway()
    async with sessionmaker() as session:
        revision = await gw._append_sync_event(
            session,
            owner_id=owner_id,
            hasn_id=owner_id,
            event_type='memory.extract_job.upserted',
            aggregate_type='memory',
            aggregate_id=f'extract_concurrency_{idx}',
            payload={'idx': idx, 'trigger_reason': 'sliding_window'},
        )
        await session.commit()
        return revision


async def test_concurrent_append_assigns_gapless_distinct_revisions(sessionmaker_pg) -> None:
    owner_id = f'h_{uuid.uuid4()}'  # 全新 owner：revision 从 1 起，与现存数据天然隔离
    try:
        results = await asyncio.gather(
            *(_append_one(sessionmaker_pg, owner_id, i) for i in range(_CONCURRENCY)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, f'并发 append 不应抛错（修复前会撞唯一约束）：{failures!r}'

        revisions = sorted(int(r) for r in results if not isinstance(r, BaseException))
        assert revisions == list(range(1, _CONCURRENCY + 1)), (
            f'revision 应连续无重复 1..{_CONCURRENCY}，实际 {revisions}'
        )

        # 落库结果也必须恰好 N 条、revision 连续无重复。
        async with sessionmaker_pg() as session:
            stored = (
                (
                    await session.execute(
                        sa.text(
                            'SELECT revision FROM public.hasn_sync_events '
                            'WHERE owner_id = :owner_id ORDER BY revision'
                        ),
                        {'owner_id': owner_id},
                    )
                )
                .scalars()
                .all()
            )
        assert [int(r) for r in stored] == list(range(1, _CONCURRENCY + 1))
    finally:
        async with sessionmaker_pg() as session:
            await session.execute(
                sa.text('DELETE FROM public.hasn_sync_events WHERE owner_id = :owner_id'),
                {'owner_id': owner_id},
            )
            await session.commit()
