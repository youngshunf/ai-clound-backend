"""主动规划闭环「恰好一次」认领真实 PG 验收（KNOWU-P4，零 mock）。

需要本地 PostgreSQL :15432（部署制品），hasn_plan.preference 表已建且含
`proactive_planned` 列 + `uq_plan_preference_owner` 唯一索引
（迁移 backend/sql/hasn/migrations/2026-06-27-plan-preference-proactive-planned.sql）。

不变量（设计 §7 / Open Q#3 事件驱动 + 幂等标记）：
- 全新 owner（无 preference 行）→ 首次 claim 返回 True（INSERT 路径），DB proactive_planned=true。
- 再次 claim → False（幂等跳过，UPDATE WHERE 不命中）。
- 已存在 preference 行（proactive_planned=false 默认）→ 首次 claim True（UPDATE 路径）。
- 并发两路 claim（独立连接）→ 恰好一方 True，另一方 False（原子 ON CONFLICT 保证）。

⚠️ claim 内部 flush（由调用方/本测试 commit 持久化）——用唯一 owner_hasn_id + finally
显式清理 hasn_plan.preference 行，避免污染 dev 库。
"""

from __future__ import annotations

import asyncio
import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_plan.model import Preference
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _cleanup(session, owner_id: str) -> None:
    await session.execute(text('DELETE FROM hasn_plan.preference WHERE owner_hasn_id = :o'), {'o': owner_id})
    await session.commit()


async def _proactive_planned(session, owner_id: str) -> bool | None:
    row = (
        await session.execute(select(Preference).where(Preference.owner_hasn_id == owner_id))
    ).scalars().first()
    return None if row is None else bool(row.proactive_planned)


async def test_claim_first_time_inserts_and_succeeds(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    try:
        # 全新 owner：无 preference 行 → 走 INSERT 路径认领
        claimed = await plan_service.claim_proactive_planning(session, owner=owner)
        await session.commit()
        assert claimed is True
        assert await _proactive_planned(session, owner) is True
    finally:
        await _cleanup(session, owner)


async def test_claim_second_time_is_idempotent(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    try:
        first = await plan_service.claim_proactive_planning(session, owner=owner)
        await session.commit()
        second = await plan_service.claim_proactive_planning(session, owner=owner)
        await session.commit()
        assert first is True
        assert second is False  # 幂等：标记已 true，WHERE 不命中
        assert await _proactive_planned(session, owner) is True
    finally:
        await _cleanup(session, owner)


async def test_claim_with_preexisting_preference_row_updates(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    try:
        # 主人已有排程偏好行（proactive_planned 默认 false）→ 走 ON CONFLICT UPDATE 路径
        await plan_service.upsert_preference(session, owner=owner, data={'timezone': 'Asia/Shanghai'})
        await session.commit()
        assert await _proactive_planned(session, owner) is False

        claimed = await plan_service.claim_proactive_planning(session, owner=owner)
        await session.commit()
        assert claimed is True
        assert await _proactive_planned(session, owner) is True

        again = await plan_service.claim_proactive_planning(session, owner=owner)
        await session.commit()
        assert again is False
    finally:
        await _cleanup(session, owner)


async def test_concurrent_claims_only_one_wins(session):
    """两路独立连接并发 claim → 原子 ON CONFLICT 保证恰好一方 True。"""
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    engine_a = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    engine_b = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sess_a = async_sessionmaker(engine_a, expire_on_commit=False)()
    sess_b = async_sessionmaker(engine_b, expire_on_commit=False)()

    async def _claim(sess) -> bool:
        result = await plan_service.claim_proactive_planning(sess, owner=owner)
        await sess.commit()
        return result

    try:
        results = await asyncio.gather(_claim(sess_a), _claim(sess_b), return_exceptions=True)
        # 并发 INSERT...ON CONFLICT 在锁争用下其中一路可能瞬时报错重试；这里两路都应正常返回布尔。
        bools = [r for r in results if isinstance(r, bool)]
        assert len(bools) == 2, f'并发返回非布尔: {results!r}'
        assert sum(1 for b in bools if b) == 1, f'应恰好一方认领成功: {bools!r}'
        assert await _proactive_planned(session, owner) is True
    finally:
        await sess_a.rollback()
        await sess_a.close()
        await sess_b.rollback()
        await sess_b.close()
        await engine_a.dispose()
        await engine_b.dispose()
        await _cleanup(session, owner)
