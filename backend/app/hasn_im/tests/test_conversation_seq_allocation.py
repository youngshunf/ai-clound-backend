"""R2-02 验收：会话内消息序号原子分配（真实 PG·零 mock）。

doc16 §4.1 权威顺序事实 = 每会话单调递增的 `conversation_seq`。分配方式为同事务内
`UPDATE hasn_conversations SET current_seq = current_seq + 1 ... RETURNING`——PG 行锁
串行化同会话并发发送。本组用真 PG（DATABASE_PORT=15432）钉三件事：

1. **并发不重复不倒退**：N 个独立 session 并发对同一会话 `allocate_seq` → 返回值恰为
   {1..N} 的排列（无重复、无空洞、无倒退）；每次提交独占推进 current_seq。
2. **唯一约束兜底**：同会话写两条相同 `conversation_seq` 的消息 → 第二条撞
   `uq_hasn_messages_conversation_seq` 唯一索引报错（落库硬约束，不靠应用层自觉）。
3. **不存在的会话**：`allocate_seq` 对缺失会话返回 None（调用方据此抛终局故障）。

PG 不可达时跳过而非硬失败；每个用例用 uuid 派生全新会话，末尾清理自身行，不污染库。
复用 event_seq 乱序防护测试（8a125cdf）的并发 gather 范式。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg():
    # NullPool：每个 session 独占连接，真实模拟并发发送方各自的连接/事务，行锁才起效
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 R2-02 序号分配测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _make_conversation(sessionmaker) -> str:
    """建一条最小合法 direct 会话，返回其 UUID 字符串（current_seq 默认 0）。"""
    conv_id = str(uuid.uuid4())
    a_id, b_id = f'h_seq{uuid.uuid4().hex[:12]}', f'a_seq{uuid.uuid4().hex[:12]}'
    async with sessionmaker() as session:
        await session.execute(
            sa.text(
                'INSERT INTO public.hasn_conversations '
                '(id, type, participant_a_id, participant_b_id, participant_a_type, '
                ' participant_b_type, status, current_seq) '
                "VALUES (:id, 'direct', :a, :b, 'human', 'agent', 'active', 0)"
            ),
            {'id': conv_id, 'a': a_id, 'b': b_id},
        )
        await session.commit()
    return conv_id


async def _cleanup(sessionmaker, conv_id: str) -> None:
    async with sessionmaker() as session:
        await session.execute(
            sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = :id'),
            {'id': conv_id},
        )
        await session.execute(
            sa.text('DELETE FROM public.hasn_conversations WHERE id = :id'),
            {'id': conv_id},
        )
        await session.commit()


async def test_concurrent_allocation_no_dup_no_regress(sessionmaker_pg) -> None:
    """N 个独立 session 并发 allocate_seq 同一会话 → 返回值恰为 {1..N} 排列（无重复/空洞/倒退）。"""
    conv_id = await _make_conversation(sessionmaker_pg)
    n = 20
    try:

        async def _alloc_once() -> int:
            # 每个协程独占 session/连接/事务：allocate（取行锁）→ commit（释锁）
            async with sessionmaker_pg() as session:
                seq = await hasn_conversations_dao.allocate_seq(session, conv_id)
                await session.commit()
                if seq is None:
                    raise AssertionError('会话存在，分配不应返回 None')
                return seq

        results = await asyncio.gather(*[_alloc_once() for _ in range(n)])

        # 核心不变量：并发分配到的 N 个 seq 恰为 1..N 的排列——行锁串行化保证无重复、无空洞
        assert sorted(results) == list(range(1, n + 1)), (
            f'并发分配应得 {{1..{n}}} 的排列，实得（已排序）：{sorted(results)}'
        )
        assert len(set(results)) == n, '不得有重复 seq'

        # current_seq 落库应恰为 N（每次分配独占 +1）
        async with sessionmaker_pg() as session:
            cur = (
                await session.execute(
                    sa.text('SELECT current_seq FROM public.hasn_conversations WHERE id = :id'),
                    {'id': conv_id},
                )
            ).scalar()
            assert cur == n, f'current_seq 应推进到 {n}，实得 {cur}'
    finally:
        await _cleanup(sessionmaker_pg, conv_id)

async def test_duplicate_conversation_seq_rejected_by_unique_index(sessionmaker_pg) -> None:
    """同会话写两条相同 conversation_seq → 唯一索引 uq_hasn_messages_conversation_seq 报错。"""
    conv_id = await _make_conversation(sessionmaker_pg)
    try:
        insert_dup = sa.text(
            'INSERT INTO public.hasn_messages '
            '(conversation_id, conversation_seq, from_id, to_id, content_type, content) '
            "VALUES (:cid, 1, 'h_x', 'a_y', 1, '{}'::jsonb)"
        )
        # 两条都硬编码 conversation_seq=1，绕过 allocate_seq 直接触碰唯一约束：
        # 第二条 execute 即被 uq_hasn_messages_conversation_seq 拒绝（落库硬约束，不到 commit 就报）
        with pytest.raises(IntegrityError):
            async with sessionmaker_pg() as session:
                await session.execute(insert_dup, {'cid': conv_id})
                await session.execute(insert_dup, {'cid': conv_id})
                await session.commit()
    finally:
        # 上面事务已因唯一约束回滚，清理只需删会话（无消息落库）
        await _cleanup(sessionmaker_pg, conv_id)


async def test_allocate_seq_returns_none_for_missing_conversation(sessionmaker_pg) -> None:
    """对不存在的会话 allocate_seq → 返回 None（调用方据此抛终局故障，不静默写坏 seq）。"""
    missing_id = str(uuid.uuid4())
    async with sessionmaker_pg() as session:
        seq = await hasn_conversations_dao.allocate_seq(session, missing_id)
        await session.rollback()
    assert seq is None, '会话不存在时 allocate_seq 必须返回 None'
