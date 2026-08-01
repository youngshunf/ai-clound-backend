"""Owner 记忆贡献与透明视图集成测试。

连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），用 savepoint 事务隔离，
结束整体回滚不留痕（符合"零 Mock 零 Fake"：连真库但不污染）。

doc19 已退役云端 LLM 内联合并，本文件只保留仍在产品契约中的贡献写入与
主人透明查询回归，防止测试继续调用已删除的 `merge_owner_memory`。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnOwnerMemoryContribution
from backend.app.hasn.service.owner_memory_service import owner_memory_service
from backend.database.db import uuid4_str

# 本地开发数据库（与 tests/hasn/conftest.py 同源，刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """事务隔离的 AsyncSession（用例结束自动回滚，绝不污染真库）。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode='create_savepoint',
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _seed_human(db: AsyncSession, *, nickname: str = 'P4主人') -> str:
    hasn_id = f'h_{uuid4_str()[:20]}'
    star_id = f's_{uuid4_str()[:12]}'
    uid = int(uuid4_str().replace('-', '')[:8], 16) % 1_000_000_000
    await db.execute(
        text(
            'INSERT INTO hasn_humans (hasn_id, star_id, user_id, nickname, avatar, bio, status, '
            'contact_policy, tags, stats, created_time, updated_time) '
            "VALUES (:hasn_id, :star_id, :uid, :nickname, '', '', 'active', "
            "'{}'::jsonb, ARRAY[]::varchar[], '{}'::jsonb, now(), now())"
        ),
        {'hasn_id': hasn_id, 'star_id': star_id, 'uid': uid, 'nickname': nickname},
    )
    await db.flush()
    return hasn_id


async def _seed_agent(db: AsyncSession, *, owner_id: str, display_name: str) -> str:
    hasn_id = f'a_{uuid4_str()[:20]}'
    star_id = f's_{uuid4_str()[:12]}'
    await db.execute(
        text(
            'INSERT INTO hasn_agents (hasn_id, star_id, owner_id, agent_name, display_name, '
            'api_key_hash, created_time) '
            'VALUES (:hasn_id, :star_id, :owner_id, :agent_name, :display_name, :api_key_hash, now())'
        ),
        {
            'hasn_id': hasn_id,
            'star_id': star_id,
            'owner_id': owner_id,
            'agent_name': display_name[:30],
            'display_name': display_name,
            'api_key_hash': uuid4_str().replace('-', '')[:64],
        },
    )
    await db.flush()
    return hasn_id


@pytest.mark.asyncio
async def test_empty_contribution_rejected(db: AsyncSession) -> None:
    """空白观察被拒，不入库。"""
    owner_id = await _seed_human(db)
    agent = await _seed_agent(db, owner_id=owner_id, display_name='空贡献分身')

    res = await owner_memory_service.contribute(db, owner_id=owner_id, agent_hasn_id=agent, content='   ')
    assert res['accepted'] is False

    contribs = (
        await db.execute(
            select(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.owner_id == owner_id)
        )
    ).scalars().all()
    assert contribs == []


@pytest.mark.asyncio
async def test_list_contributions_orders_desc_and_counts_pending(db: AsyncSession) -> None:
    """Owner 透明视图：贡献按时间倒序，未合并贡献如实计数。"""
    owner_id = await _seed_human(db)
    agent_a = await _seed_agent(db, owner_id=owner_id, display_name='分身A')
    agent_b = await _seed_agent(db, owner_id=owner_id, display_name='分身B')

    await owner_memory_service.contribute(db, owner_id=owner_id, agent_hasn_id=agent_a, content='观察一')
    await owner_memory_service.contribute(db, owner_id=owner_id, agent_hasn_id=agent_b, content='观察二')

    await owner_memory_service.contribute(db, owner_id=owner_id, agent_hasn_id=agent_a, content='观察三-新')

    listing = await owner_memory_service.list_contributions(db, owner_id=owner_id, limit=50)
    assert len(listing['items']) == 3
    # 倒序：最新（观察三-新）在最前
    assert listing['items'][0]['content'] == '观察三-新'
    assert listing['items'][0]['status'] == 'pending'
    assert listing['pending_count'] == 3
    assert all(item['status'] == 'pending' for item in listing['items'])
    assert all(item['merged_into_version'] is None for item in listing['items'])
