"""演示文稿（模块 17）云端数据层集成测试：deck 模型 + 独立 PG schema=deck。

连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），savepoint 事务隔离，结束整体回滚不留痕
（符合"零 Mock 零 Fake"：连真库但不污染）。验证：
- 模型落到独立 schema `deck.deck`（非 public）；
- bigint 自增主键（对齐 fba id_key）；created_time 自动写入；
- owner_id 列支持 owner 隔离查询（A 看不到 B 的 deck）；
- deleted_time 软删（不物理删，过滤未删行）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.deck.model.deck import Deck
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

# 本地开发数据库（与 tests/hasn/conftest.py 同源，刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """事务隔离的 AsyncSession（用例结束自动回滚，绝不污染真库）。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode='create_savepoint')
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


def _new_deck(owner_id: str, title: str) -> Deck:
    return Deck(owner_id=owner_id, title=title, status='draft', language='zh', source='manual')


async def _owner_deck_count(db: AsyncSession, owner_id: str) -> int:
    stmt = select(func.count()).select_from(Deck).where(Deck.owner_id == owner_id, Deck.deleted_time.is_(None))
    return int((await db.execute(stmt)).scalar_one())


def test_deck_maps_to_independent_schema() -> None:
    # Arrange / Act：模型元数据（纯元数据断言，无需 DB/await）
    # Assert：落到独立 schema deck.deck，bigint 主键
    assert Deck.__table__.schema == 'deck'
    assert Deck.__table__.fullname == 'deck.deck'
    assert Deck.__table__.c.id.type.__class__.__name__ == 'BigInteger'


@pytest.mark.asyncio
async def test_deck_owner_isolation_and_soft_delete(db: AsyncSession) -> None:
    # Arrange：A 两个 deck，B 一个 deck
    owner_a = f'h_{uuid4_str()}'  # 38 字符，贴近 varchar(40) 真实长度
    owner_b = f'h_{uuid4_str()}'
    a1 = _new_deck(owner_a, 'A 的演示稿一')
    a2 = _new_deck(owner_a, 'A 的演示稿二')
    b1 = _new_deck(owner_b, 'B 的演示稿')
    db.add_all([a1, a2, b1])
    await db.flush()

    # Assert：bigint 自增主键已分配 + created_time 自动写入
    assert isinstance(a1.id, int) and a1.id > 0
    assert a1.created_time is not None
    assert a1.__table__.fullname == 'deck.deck'

    # Assert：owner 隔离（A 看到 2，B 看到 1）
    assert await _owner_deck_count(db, owner_a) == 2
    assert await _owner_deck_count(db, owner_b) == 1

    # Act：软删 A 的一个 deck（不物理删）
    a1.deleted_time = timezone.now()
    await db.flush()

    # Assert：A 未删行变 1；行仍在库（软删非物理删）
    assert await _owner_deck_count(db, owner_a) == 1
    total_a = (
        await db.execute(select(func.count()).select_from(Deck).where(Deck.owner_id == owner_a))
    ).scalar_one()
    assert int(total_a) == 2  # 软删行仍存在
