"""演示文稿（模块 17）重排序回归：update_page 改 position 撞 (deck_id, position) 唯一约束修复。

连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），savepoint 事务隔离，结束整体回滚不留痕
（符合"零 Mock 零 Fake"：连真库但不污染）。

复现并锁死的 bug：重排序由 daemon 同步 / webui 逐页 PUT 绝对 position 实现，逐条落库时
中间态会让两页短暂落在同一 position，撞 `(deck_id, position) WHERE deleted_time IS NULL`
部分唯一索引 → asyncpg UniqueViolationError → 接口 500（生产日志原始报错）。
修复：DeckService.update_page 改 position 撞位时在同一事务内两段式原子交换。验证：
- 单次 update 把页移到被占位 → 与占位页交换，无 IntegrityError，两页位置对调；
- 逐页推送整套新序（swap 任意推送顺序都收敛到目标排列）→ 无报错，最终序正确；
- 任何时刻未删页 (deck_id, position) 仍唯一（部分唯一索引未被破坏）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_deck.model.deck import Deck
from backend.app.hasn_deck.model.page import Page
from backend.app.hasn_deck.service.deck_service import Subject, deck_service
from backend.database.db import uuid4_str

# 本地开发数据库（与 test_deck_model.py 同源，刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture
async def db() -> AsyncSession:
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
    return f'h_{uuid4_str()}'


async def _seed_deck(db: AsyncSession, owner: str, n: int) -> tuple[int, list[int]]:
    """建 deck + n 页（position 0..n-1），返回 (deck_id, page_ids 按 position 升序)。"""
    deck = Deck(owner_id=owner, title='重排序稿', status='draft', language='zh', source='manual')
    db.add(deck)
    await db.flush()
    page_ids: list[int] = []
    for pos in range(n):
        p = Page(deck_id=deck.id, owner_id=owner, position=pos, title=f'P{pos}', html=f'<i>{pos}</i>', status='generated')
        db.add(p)
        await db.flush()
        page_ids.append(p.id)
    return deck.id, page_ids


async def _positions(db: AsyncSession, deck_id: int) -> dict[int, int]:
    """返回 {page_id: position}（仅未删页）。"""
    rows = (
        await db.execute(
            select(Page.id, Page.position).where(Page.deck_id == deck_id, Page.deleted_time.is_(None))
        )
    ).all()
    return {pid: pos for pid, pos in rows}


@pytest.mark.asyncio
async def test_update_page_to_occupied_position_swaps_occupant(db: AsyncSession) -> None:
    # Arrange：deck 两页 P0@0 / P1@1
    owner = _owner()
    deck_id, [p0, p1] = await _seed_deck(db, owner, 2)

    # Act：把 P0 移到被 P1 占用的 position 1（旧代码会撞唯一约束 500）
    await deck_service.update_page(db, subject=Subject.human(owner), page_id=p0, fields={'position': 1})

    # Assert：两页对调，位置仍唯一、无空缺
    pos = await _positions(db, deck_id)
    assert pos[p0] == 1
    assert pos[p1] == 0
    assert sorted(pos.values()) == [0, 1]


@pytest.mark.asyncio
async def test_per_page_reorder_push_converges_no_collision(db: AsyncSession) -> None:
    # Arrange：deck 三页 P0@0 / P1@1 / P2@2
    owner = _owner()
    deck_id, [p0, p1, p2] = await _seed_deck(db, owner, 3)

    # Act：模拟 daemon push_deck_reorder 逐页推绝对 position——目标轮转序 P0→1, P1→2, P2→0。
    # 任何中间态都不得撞 (deck_id, position) 唯一约束（旧代码第一步就 500）。
    target = {p0: 1, p1: 2, p2: 0}
    for page_id, new_pos in target.items():
        await deck_service.update_page(
            db,
            subject=Subject.human(owner),
            page_id=page_id,
            fields={'position': new_pos},
        )

    # Assert：最终序恰为目标排列，且未删页位置无重复
    pos = await _positions(db, deck_id)
    assert pos == target
    assert sorted(pos.values()) == [0, 1, 2]


@pytest.mark.asyncio
async def test_full_reverse_reorder_any_push_order(db: AsyncSession) -> None:
    # Arrange：deck 四页 P0..P3 @ 0..3
    owner = _owner()
    deck_id, ids = await _seed_deck(db, owner, 4)
    p0, p1, p2, p3 = ids

    # Act：整体反转 P0→3, P1→2, P2→1, P3→0；刻意打乱推送顺序验证 swap 收敛性
    target = {p0: 3, p1: 2, p2: 1, p3: 0}
    for page_id in (p2, p0, p3, p1):  # 乱序推送
        await deck_service.update_page(
            db,
            subject=Subject.human(owner),
            page_id=page_id,
            fields={'position': target[page_id]},
        )

    # Assert：最终序正确，位置唯一连续
    pos = await _positions(db, deck_id)
    assert pos == target
    assert sorted(pos.values()) == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_update_page_position_with_other_fields(db: AsyncSession) -> None:
    # Arrange：deck 两页
    owner = _owner()
    deck_id, [p0, p1] = await _seed_deck(db, owner, 2)

    # Act：一次 PUT 同时改 position（撞位）+ 其它字段（webui 编辑常见形态）
    await deck_service.update_page(
        db,
        subject=Subject.human(owner),
        page_id=p0,
        fields={'position': 1, 'title': '改名了', 'html': None},
    )

    # Assert：位置交换成功 + 其它字段已写 + None 字段不覆盖
    pos = await _positions(db, deck_id)
    assert pos[p0] == 1 and pos[p1] == 0
    moved = (await db.execute(select(Page).where(Page.id == p0))).scalar_one()
    assert moved.title == '改名了'
    assert moved.html == '<i>0</i>'  # html=None 未覆盖原值
