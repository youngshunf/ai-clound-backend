"""演示文稿（模块 17）云端数据层集成测试：5 张表 + 独立 PG schema=deck。

连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），savepoint 事务隔离，结束整体回滚不留痕
（符合"零 Mock 零 Fake"：连真库但不污染）。验证：
- deck/page/asset/revision/style_profile 全部落到独立 schema `deck.*`（非 public）；
- bigint 自增主键（对齐 fba id_key）；created_time 自动写入；
- owner_id 列支持 owner 隔离查询（A 看不到 B 的数据）；
- deleted_time 软删（不物理删，过滤未删行）；
- page (deck_id, position) 未删页内部分唯一；revision (deck_id, seq) 全唯一；
  style_profile (owner_id, slug) 未删行部分唯一——真实约束由真库强制；
- revision.snapshot JSONB 往返一致。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.deck.model.asset import Asset
from backend.app.deck.model.deck import Deck
from backend.app.deck.model.page import Page
from backend.app.deck.model.revision import Revision
from backend.app.deck.model.style_profile import StyleProfile
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

# 本地开发数据库（与 tests/hasn/conftest.py 同源，刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'

ALL_MODELS = (Deck, Page, Asset, Revision, StyleProfile)


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


def _owner() -> str:
    return f'h_{uuid4_str()}'  # 38 字符，贴近 varchar(40) 真实长度


def _new_deck(owner_id: str, title: str) -> Deck:
    return Deck(owner_id=owner_id, title=title, status='draft', language='zh', source='manual')


async def _owner_deck_count(db: AsyncSession, owner_id: str) -> int:
    stmt = select(func.count()).select_from(Deck).where(Deck.owner_id == owner_id, Deck.deleted_time.is_(None))
    return int((await db.execute(stmt)).scalar_one())


def test_all_deck_models_map_to_independent_schema() -> None:
    # Arrange / Act：模型元数据（纯元数据断言，无需 DB/await）
    # Assert：5 张表全部落到独立 schema deck.*，bigint 主键
    for model in ALL_MODELS:
        table = model.__table__
        assert table.schema == 'deck', f'{model.__name__} 未落到 deck schema'
        assert table.fullname == f'deck.{table.name}'
        assert table.c.id.type.__class__.__name__ == 'BigInteger', f'{model.__name__}.id 非 bigint'


@pytest.mark.asyncio
async def test_deck_owner_isolation_and_soft_delete(db: AsyncSession) -> None:
    # Arrange：A 两个 deck，B 一个 deck
    owner_a, owner_b = _owner(), _owner()
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


@pytest.mark.asyncio
async def test_page_links_to_deck_and_position_partial_unique(db: AsyncSession) -> None:
    # Arrange：建 deck + 两页（position 0/1）
    owner = _owner()
    deck = _new_deck(owner, '带页的演示稿')
    db.add(deck)
    await db.flush()
    p0 = Page(deck_id=deck.id, owner_id=owner, position=0, title='封面', html='<i>0</i>', status='generated')
    p1 = Page(deck_id=deck.id, owner_id=owner, position=1, title='正文', html='<i>1</i>', status='generated')
    db.add_all([p0, p1])
    await db.flush()

    # Assert：两页挂在同一 deck 下
    page_count = (
        await db.execute(select(func.count()).select_from(Page).where(Page.deck_id == deck.id))
    ).scalar_one()
    assert int(page_count) == 2

    # Assert：未删页内 (deck_id, position) 唯一——真库部分唯一索引强制
    dup = Page(deck_id=deck.id, owner_id=owner, position=0, title='冲突', html='<section/>', status='empty')
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(dup)
            await db.flush()

    # Act：软删 p0 后，新页可再占 position 0（部分唯一仅约束未删行）
    p0.deleted_time = timezone.now()
    await db.flush()
    p0b = Page(deck_id=deck.id, owner_id=owner, position=0, title='新封面', html='<i>0b</i>', status='edited')
    db.add(p0b)
    await db.flush()  # 不应抛错
    assert isinstance(p0b.id, int) and p0b.id > 0


@pytest.mark.asyncio
async def test_revision_seq_unique_and_jsonb_roundtrip(db: AsyncSession) -> None:
    # Arrange：建 deck + 两个版本快照
    owner = _owner()
    deck = _new_deck(owner, '版本演示稿')
    db.add(deck)
    await db.flush()
    did = deck.id  # 捕获，后续 expire_all 后不再触碰 deck 属性（避免同步 lazy refresh）
    snap1 = {'deck': {'title': 'v1'}, 'pages': [{'position': 0, 'html': '<i>1</i>'}]}
    snap2 = {'deck': {'title': 'v2'}, 'pages': []}
    r1 = Revision(deck_id=did, owner_id=owner, seq=1, label='generated', snapshot=snap1)
    r2 = Revision(deck_id=did, owner_id=owner, seq=2, label='edited', snapshot=snap2)
    db.add_all([r1, r2])
    await db.flush()

    # Assert：JSONB 往返一致（捕获 id 后 expire_all，强制从真库重读）
    rid = r1.id
    db.expire_all()
    reloaded = (await db.execute(select(Revision).where(Revision.id == rid))).scalar_one()
    assert reloaded.snapshot == snap1
    assert reloaded.snapshot['pages'][0]['position'] == 0

    # Assert：(deck_id, seq) 全唯一——真库唯一索引强制
    dup = Revision(deck_id=did, owner_id=owner, seq=1, label='manual', snapshot={})
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(dup)
            await db.flush()


@pytest.mark.asyncio
async def test_asset_links_to_deck_and_owner_isolation(db: AsyncSession) -> None:
    # Arrange：A 的 deck 挂两个资产，B 的 deck 挂一个
    owner_a, owner_b = _owner(), _owner()
    deck_a = _new_deck(owner_a, 'A 资产稿')
    deck_b = _new_deck(owner_b, 'B 资产稿')
    db.add_all([deck_a, deck_b])
    await db.flush()
    db.add_all(
        [
            Asset(deck_id=deck_a.id, owner_id=owner_a, kind='image', uri='hasn://asset/x1'),
            Asset(deck_id=deck_a.id, owner_id=owner_a, kind='export-pptx', uri='hasn://asset/x2'),
            Asset(deck_id=deck_b.id, owner_id=owner_b, kind='thumb', uri='hasn://asset/y1'),
        ]
    )
    await db.flush()

    a_count = (
        await db.execute(select(func.count()).select_from(Asset).where(Asset.owner_id == owner_a))
    ).scalar_one()
    b_count = (
        await db.execute(select(func.count()).select_from(Asset).where(Asset.owner_id == owner_b))
    ).scalar_one()
    assert int(a_count) == 2
    assert int(b_count) == 1


@pytest.mark.asyncio
async def test_style_profile_owner_slug_partial_unique(db: AsyncSession) -> None:
    # Arrange：owner A 建样式 minimal
    owner_a, owner_b = _owner(), _owner()
    sp = StyleProfile(slug='minimal', label='极简', source='custom', owner_id=owner_a)
    db.add(sp)
    await db.flush()

    # Assert：同 owner 同 slug 未删行唯一——真库部分唯一索引强制
    dup = StyleProfile(slug='minimal', label='极简2', source='custom', owner_id=owner_a)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(dup)
            await db.flush()

    # Assert：不同 owner 可同名 slug（owner 隔离）
    other = StyleProfile(slug='minimal', label='B 的极简', source='custom', owner_id=owner_b)
    db.add(other)
    await db.flush()
    assert isinstance(other.id, int) and other.id > 0

    # Act：软删 A 的 minimal 后可重建同名（部分唯一仅约束未删行）
    sp.deleted_time = timezone.now()
    await db.flush()
    revived = StyleProfile(slug='minimal', label='A 的新极简', source='custom', owner_id=owner_a)
    db.add(revived)
    await db.flush()
    assert isinstance(revived.id, int) and revived.id > 0
