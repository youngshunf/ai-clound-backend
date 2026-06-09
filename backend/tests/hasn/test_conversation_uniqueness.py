"""hasn_conversations 唯一性收敛回归（真实 PG，零 mock）。

防三类回归（对应 fix/conversation-uniqueness）：
1. **清洗迁移**：存量同一对参与者的多行 direct 会话被合并为最早 created_time 的
   canonical 行，引用表（messages/sync_events）的 conversation_id 被重指到 canonical，
   重复行删除；迁移可重复执行（幂等）；执行后 partial unique index 存在。
2. **并发不重复**：N 个并发 session 对同一全新参与者对调 get_or_create_conversation，
   靠事务级 advisory lock 串行化查改，最终只产生一行（修复前两事务都 SELECT 空、双双
   INSERT → 两行；有了 unique index 还会撞约束抛错）。
3. **relation_type 漂移归一**：同一对参与者用不同 relation_type 调 get_or_create，
   返回同一个 conversation（relation_type 不再参与去重键）。

需要本地开发 PG（export DATABASE_PORT=15432）；PG 不可达时跳过而非硬失败。
每个用例用 uuid 派生的全新参与者，末尾清理自身行，不污染库。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn.service.message_router import get_or_create_conversation
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_CONCURRENCY = 12

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / 'sql'
    / 'hasn'
    / 'migrations'
    / '2026-06-09-conversation-uniqueness.sql'
)


def _migration_statements() -> list[str]:
    """把迁移 SQL 拆成可逐条执行的语句：去掉整行 `--` 注释后按 `;` 切分。

    迁移内每条语句仅在末尾有 `;`（inline CTE，内部无分号），故按 `;` 切分安全。
    """
    raw = _MIGRATION.read_text(encoding='utf-8')
    no_comments = '\n'.join(
        line for line in raw.splitlines() if not line.lstrip().startswith('--')
    )
    return [stmt.strip() for stmt in no_comments.split(';') if stmt.strip()]


@pytest_asyncio.fixture
async def sessionmaker_pg():
    # NullPool：每个 session 独占真实连接，asyncio.gather 才有真正并发的事务。
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过会话唯一性回归：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _apply_migration(session) -> None:
    for stmt in _migration_statements():
        await session.execute(sa.text(stmt))


async def _cleanup_pair(sessionmaker, a_id: str, b_id: str) -> None:
    async with sessionmaker() as session:
        ids = (
            (
                await session.execute(
                    sa.text(
                        'SELECT id FROM public.hasn_conversations '
                        'WHERE participant_a_id IN (:a, :b) OR participant_b_id IN (:a, :b)'
                    ),
                    {'a': a_id, 'b': b_id},
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await session.execute(
                sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:ids)'),
                {'ids': list(ids)},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:ids)'),
                {'ids': list(ids)},
            )
        await session.commit()


def _fresh_pair() -> tuple[str, str]:
    a, b = sorted([f'h_cu{uuid.uuid4().hex[:18]}', f'h_cu{uuid.uuid4().hex[:18]}'])
    return a, b


async def test_migration_dedups_repoints_and_is_idempotent(sessionmaker_pg):
    a_id, b_id = _fresh_pair()
    base = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
    try:
        async with sessionmaker_pg() as session:
            # 清干净 partial unique index，才能播种重复行（模拟修复前的存量脏数据）
            await session.execute(sa.text('DROP INDEX IF EXISTS uq_hasn_conversations_direct'))
            await session.execute(sa.text('DROP INDEX IF EXISTS uq_hasn_conversations_group'))

            # 同一对参与者播 3 行 direct（不同 relation_type），created_time 递增
            seeded: list = []
            for rel in ('social', 'service', 'commerce'):
                conv = HasnConversations(
                    type='direct',
                    relation_type=rel,
                    participant_a_id=a_id,
                    participant_b_id=b_id,
                    participant_a_type='human',
                    participant_b_type='human',
                    status='active',
                )
                session.add(conv)
                await session.flush()
                seeded.append(conv.id)
            # 用确定的递增 created_time 固定 canonical = 第一行
            for offset, cid in enumerate(seeded):
                await session.execute(
                    sa.text('UPDATE public.hasn_conversations SET created_time = :t WHERE id = :id'),
                    {'t': base + timedelta(minutes=offset), 'id': cid},
                )
            canon_id = seeded[0]

            # 一条消息指向最后那行（非 canonical），迁移须把它重指到 canonical
            msg = HasnMessages(conversation_id=seeded[-1], from_id=a_id, to_id=b_id)
            session.add(msg)
            await session.flush()
            msg_id = msg.id
            await session.commit()

        # 应用迁移
        async with sessionmaker_pg() as session:
            await _apply_migration(session)
            await session.commit()

        async with sessionmaker_pg() as session:
            remaining = (
                (
                    await session.execute(
                        sa.text(
                            'SELECT id FROM public.hasn_conversations '
                            'WHERE participant_a_id = :a AND participant_b_id = :b AND type = :t'
                        ),
                        {'a': a_id, 'b': b_id, 't': 'direct'},
                    )
                )
                .scalars()
                .all()
            )
            assert [str(x) for x in remaining] == [str(canon_id)], (
                f'去重后该对参与者应只剩 canonical 一行，实际 {remaining}'
            )

            repointed = (
                await session.execute(
                    sa.text('SELECT conversation_id FROM public.hasn_messages WHERE id = :id'),
                    {'id': msg_id},
                )
            ).scalar()
            assert str(repointed) == str(canon_id), '消息应被重指到 canonical 会话'

            idx = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'hasn_conversations' "
                            "AND indexname IN ('uq_hasn_conversations_direct','uq_hasn_conversations_group')"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert set(idx) == {
                'uq_hasn_conversations_direct',
                'uq_hasn_conversations_group',
            }, f'partial unique index 应已建立，实际 {idx}'

        # 幂等：再次应用迁移不报错，仍只一行
        async with sessionmaker_pg() as session:
            await _apply_migration(session)
            await session.commit()
        async with sessionmaker_pg() as session:
            count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_conversations '
                        'WHERE participant_a_id = :a AND participant_b_id = :b AND type = :t'
                    ),
                    {'a': a_id, 'b': b_id, 't': 'direct'},
                )
            ).scalar()
            assert count == 1, '迁移幂等：重复执行仍只一行'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)


async def _get_or_create_one(sessionmaker, a_id, b_id, relation_type) -> str:
    async with sessionmaker() as session:
        conv = await get_or_create_conversation(
            session, a_id, 'human', b_id, 'human', relation_type
        )
        await session.commit()
        return str(conv.id)


async def test_concurrent_get_or_create_yields_single_row(sessionmaker_pg):
    a_id, b_id = _fresh_pair()
    try:
        # 确保 unique index 在位：并发若回归，会撞约束抛错而非静默产生两行
        async with sessionmaker_pg() as session:
            await _apply_migration(session)
            await session.commit()

        results = await asyncio.gather(
            *(_get_or_create_one(sessionmaker_pg, a_id, b_id, 'social') for _ in range(_CONCURRENCY)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, f'并发 get_or_create 不应抛错（修复前会撞唯一约束）：{failures!r}'
        assert len(set(results)) == 1, f'并发应收敛到同一 conversation_id，实际 {set(results)}'

        async with sessionmaker_pg() as session:
            count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_conversations '
                        'WHERE participant_a_id = :a AND participant_b_id = :b AND type = :t'
                    ),
                    {'a': a_id, 'b': b_id, 't': 'direct'},
                )
            ).scalar()
            assert count == 1, f'同一对参与者应只一行，实际 {count}'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)


async def test_relation_type_drift_converges(sessionmaker_pg):
    a_id, b_id = _fresh_pair()
    try:
        async with sessionmaker_pg() as session:
            await _apply_migration(session)
            await session.commit()

        id_social = await _get_or_create_one(sessionmaker_pg, a_id, b_id, 'social')
        id_service = await _get_or_create_one(sessionmaker_pg, a_id, b_id, 'service')
        assert id_social == id_service, (
            'relation_type 漂移不应分裂会话：social 与 service 须复用同一行'
        )

        async with sessionmaker_pg() as session:
            count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_conversations '
                        'WHERE participant_a_id = :a AND participant_b_id = :b AND type = :t'
                    ),
                    {'a': a_id, 'b': b_id, 't': 'direct'},
                )
            ).scalar()
            assert count == 1, f'relation_type 漂移后仍只一行，实际 {count}'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)
