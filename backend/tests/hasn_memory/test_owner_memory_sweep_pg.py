"""Owner 记忆 pending 合并兜底重试 sweeper 真实 PG 验收（MEMFIX-3，零 mock）。

同步内联合并失败时贡献留 pending；本 sweeper 周期重跑合并。验证：
- 滞留 pending（已超 min_age）被重跑合并 → contribution 翻 merged + owner_memory version+1；
- 刚 contribute 的新 pending（未超 min_age）不被 sweep 抢（避开同步内联热路径）；
- 合并失败（LLM 仍挂）单 owner 不影响其余、pending 留待下轮（不产生假合并）。

LLM 用 service 既有的注入式 ``llm_complete`` 打桩（与 merge_owner_memory 同一 sanctioned 注入点，
非业务 mock——不伪造任何主人画像，只替换出网关那一跳让用例确定）。需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from typing import TYPE_CHECKING, NoReturn

import pytest
import pytest_asyncio

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.model import HasnOwnerMemory
from backend.app.hasn_memory.model.owner_memory import HasnOwnerMemoryContribution
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    # pytest-asyncio 为每个用例创建独立事件循环；先换出前一循环遗留的全局连接池，
    # 避免 sweeper 复用绑定到旧循环的 asyncpg 连接。close=False 不在当前循环关闭旧连接。
    await async_engine.dispose(close=False)
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
        # sweep_pending_merges 内部用全局 async_db_session（生产真实行为）；pytest-asyncio 每测试
        # 新事件循环，全局引擎连接池绑在上一循环 → 下个测试报 different loop。每测试后释放全局池。
        await async_engine.dispose()


async def _seed_pending(session: AsyncSession, owner_id: str, *, content: str, age_seconds: int) -> int:
    """落一条 pending contribution 并把 created_time 老化 age_seconds 秒（created_time 为 init=False，
    不能构造时传，故插入后 UPDATE 老化）。返回 contribution id。"""
    row = HasnOwnerMemoryContribution(
        owner_id=owner_id, agent_hasn_id='a_sweep_test', content=content, status='pending'
    )
    session.add(row)
    await session.flush()
    cid = int(row.id)
    aged = timezone.now() - timedelta(seconds=age_seconds)
    await session.execute(
        update(HasnOwnerMemoryContribution)
        .where(HasnOwnerMemoryContribution.id == cid)
        .values(created_time=aged)
    )
    await session.commit()
    return cid


async def _cleanup(session: AsyncSession, owner_id: str) -> None:
    await session.execute(
        delete(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.owner_id == owner_id)
    )
    await session.execute(delete(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id))
    await session.commit()


async def test_sweep_merges_stranded_pending(session: AsyncSession) -> None:
    """滞留 pending（已超 min_age）被 sweep 重跑合并：contribution 翻 merged + owner_memory version+1。"""
    owner = f'h_sweep_{uuid.uuid4().hex[:8]}'
    cid = await _seed_pending(session, owner, content='主人常驻昆明，注重健康抗衰老。', age_seconds=300)

    async def _merge_ok(_messages: list[dict[str, str]]) -> str:  # noqa: RUF029 — 须 async 以匹配 llm_complete Awaitable 协议
        return '健康: 主人注重健康与抗衰老\n§\n居住: 主人常驻昆明'

    try:
        summary = await owner_memory_service.sweep_pending_merges(
            min_age_seconds=120, max_owners=50, owner_ids=[owner], llm_complete=_merge_ok
        )
        assert summary['merged'] >= 1
        # 该 owner 的 contribution 已翻 merged
        row = (
            await session.execute(
                select(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.id == cid)
            )
        ).scalar_one()
        await session.refresh(row)
        assert row.status == 'merged'
        assert row.merged_into_version == 1
        # owner_memory 落库、version=1、内容为合并结果
        mem = (
            await session.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner))
        ).scalar_one()
        assert mem.version == 1
        assert mem.content and '抗衰老' in mem.content
        # 身份兜底：_merge_ok 故意没返回 HASN ID，合并下发前应自动补回 Owner HASN ID（owner_id）——
        # 这是「合并抹掉主人 HASN_ID 等建档身份」数据丢失 bug 的回归守卫，走真实 PG 合并路径。
        assert 'Owner HASN ID' in mem.content
        assert owner in mem.content
    finally:
        await _cleanup(session, owner)


async def test_sweep_skips_fresh_pending(session: AsyncSession) -> None:
    """刚 contribute 的新 pending（未超 min_age）不被 sweep 抢——避开同步内联热路径双合并。"""
    owner = f'h_sweep_{uuid.uuid4().hex[:8]}'
    cid = await _seed_pending(session, owner, content='主人喜欢登山。', age_seconds=5)

    async def _merge_should_not_run(_messages: list[dict[str, str]]) -> NoReturn:  # noqa: RUF029 — 须 async 以匹配 llm_complete Awaitable 协议
        raise AssertionError('新鲜 pending 不应被 sweep 触发合并')

    try:
        summary = await owner_memory_service.sweep_pending_merges(
            min_age_seconds=120, max_owners=50, owner_ids=[owner], llm_complete=_merge_should_not_run
        )
        # 本 owner 不入候选（其最老 pending 仅 5s）；该 contribution 仍 pending、无 owner_memory
        row = (
            await session.execute(
                select(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.id == cid)
            )
        ).scalar_one()
        assert row.status == 'pending'
        assert (
            await session.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner))
        ).scalar_one_or_none() is None
        assert isinstance(summary['candidates'], int)
    finally:
        await _cleanup(session, owner)


async def test_sweep_merge_failure_keeps_pending(session: AsyncSession) -> None:
    """合并失败（LLM 仍挂）：summary.failed 计入、contribution 留 pending、无假合并。"""
    owner = f'h_sweep_{uuid.uuid4().hex[:8]}'
    cid = await _seed_pending(session, owner, content='主人在深圳南山工作。', age_seconds=300)

    async def _merge_boom(_messages: list[dict[str, str]]) -> NoReturn:  # noqa: RUF029 — 须 async 以匹配 llm_complete Awaitable 协议
        raise RuntimeError('simulated LLM gateway failure')

    try:
        summary = await owner_memory_service.sweep_pending_merges(
            min_age_seconds=120, max_owners=50, owner_ids=[owner], llm_complete=_merge_boom
        )
        assert summary['failed'] >= 1
        row = (
            await session.execute(
                select(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.id == cid)
            )
        ).scalar_one()
        assert row.status == 'pending'  # 失败不丢贡献、不产生假合并
        assert (
            await session.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner))
        ).scalar_one_or_none() is None
    finally:
        await _cleanup(session, owner)
