"""P0-3 企业应用命名席位 service 真实 PostgreSQL 测试（零 mock）。

覆盖（实施清单 §P0-3 验收 + 设计 §6.2/§6.4/§6.5）：
- assign_seat：指派→计数=1；满席指派抛 seats_exhausted；重复指派/非成员拒
- release_seat：回收后计数递减；幂等回收不报错
- release_all_seats_for_member：释放成员在企业全部应用席位（P4 用）
- settle_seat_purchase：seats_total 累加（首购 + 扩容同一路径，S2）
- shrink_seats：new_seats_total < seats_used 拒（must_release_seats_first，M4）

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_seat_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_member(db: AsyncSession, *, enterprise_id: int, role: str = 'member', approved: bool = True) -> str:
    """造一个企业成员（HasnHumans + membership），返回其 owner hasn_id。"""
    user_id = 920_000_000 + int(_uid(), 16) % 1_000_000
    hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'seat member {_uid()}'))
    db.add(
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=user_id,
            role=role,
            status='approved' if approved else 'pending',
        )
    )
    await db.flush()
    return hasn_id


async def _seed_enterprise(db: AsyncSession) -> int:
    ent = HasnEnterprise(name=f'席位测试企业 {_uid()}', slug=f'seat-{_uid()}', owner_user_id=0)
    db.add(ent)
    await db.flush()
    return ent.id


async def _seed_entitlement(
    db: AsyncSession, *, app_id: str, enterprise_id: int, seats_total: int | None
) -> HasnAppEntitlement:
    ent = HasnAppEntitlement(
        app_id=app_id,
        subject_type='enterprise',
        subject_id=str(enterprise_id),
        source='purchase',
        status='active',
        seats_total=seats_total,
        expires_at=timezone.now() + timedelta(days=30),
    )
    db.add(ent)
    await db.flush()
    return ent


# ============================ assign / count ============================


async def test_assign_then_count(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    entitlement = await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=2)
    m1 = await _seed_member(db, enterprise_id=ent_id)
    m2 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 1

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m2, assigned_by='admin')
    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 2


async def test_assign_full_raises_seats_exhausted(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=1)
    m1 = await _seed_member(db, enterprise_id=ent_id)
    m2 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    with pytest.raises(errors.RequestError) as exc:
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m2, assigned_by='admin'
        )
    assert app_seat_service.SEATS_EXHAUSTED in exc.value.msg


async def test_assign_duplicate_member_raises(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=3)
    m1 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    with pytest.raises(errors.RequestError):
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin'
        )


async def test_assign_non_member_raises(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=3)
    # 建了 HasnHumans 但没在本企业 approved（换一个企业 id）
    outsider = await _seed_member(db, enterprise_id=ent_id + 999_999)
    with pytest.raises(errors.RequestError) as exc:
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=outsider, assigned_by='admin'
        )
    assert '名册' in exc.value.msg


async def test_assign_without_entitlement_raises(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    m1 = await _seed_member(db, enterprise_id=ent_id)
    with pytest.raises(errors.RequestError):
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin'
        )


# ============================ release ============================


async def test_release_decrements_count(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    entitlement = await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=2)
    m1 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 1

    released = await app_seat_service.release_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1)
    assert released is True
    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 0


async def test_release_idempotent(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=2)
    m1 = await _seed_member(db, enterprise_id=ent_id)
    # 从未指派 → 回收返回 False，不报错
    assert await app_seat_service.release_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1) is False


async def test_release_then_reassign_ok(db: AsyncSession) -> None:
    """回收后可再次指派同一成员（uq_app_seat_active 只挡 assigned 状态）。"""
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=1)
    m1 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    await app_seat_service.release_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1)
    # 满席=1 但已回收 → 重新指派应成功
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')


async def test_release_all_seats_for_member(db: AsyncSession) -> None:
    app_a = f'seat_{_uid()}'
    app_b = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_a, enterprise_id=ent_id, seats_total=2)
    await _seed_entitlement(db, app_id=app_b, enterprise_id=ent_id, seats_total=2)
    m1 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_a, member_hasn_id=m1, assigned_by='admin')
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_b, member_hasn_id=m1, assigned_by='admin')

    released = await app_seat_service.release_all_seats_for_member(db, enterprise_id=ent_id, member_hasn_id=m1)
    assert released == 2
    # 再释放一次 → 0（幂等）
    assert await app_seat_service.release_all_seats_for_member(db, enterprise_id=ent_id, member_hasn_id=m1) == 0


# ============================ settle_seat_purchase (S2 accumulate) ============================


async def test_settle_seat_purchase_accumulates(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    # 首购：无 entitlement → grant + seats_total = 3
    ent1 = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=3, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    assert ent1.seats_total == 3
    # 扩容：同一 entitlement 上累加 +2 → 5
    ent2 = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=2, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    assert ent2.id == ent1.id
    assert ent2.seats_total == 5


async def test_settle_rejects_non_positive_seats(db: AsyncSession) -> None:
    ent_id = await _seed_enterprise(db)
    with pytest.raises(errors.RequestError):
        await app_seat_service.settle_seat_purchase(
            db, enterprise_id=ent_id, app_id=f'seat_{_uid()}', seats=0, billing_cycle='month', order_ref='o-x'
        )


# ============================ shrink guard (M4) ============================


async def test_shrink_below_used_raises(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=3)
    m1 = await _seed_member(db, enterprise_id=ent_id)
    m2 = await _seed_member(db, enterprise_id=ent_id)
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m1, assigned_by='admin')
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=m2, assigned_by='admin')

    # 已指派 2，缩到 1 应拒
    with pytest.raises(errors.RequestError) as exc:
        await app_seat_service.shrink_seats(db, enterprise_id=ent_id, app_id=app_id, new_seats_total=1)
    assert app_seat_service.MUST_RELEASE_FIRST in exc.value.msg

    # 缩到 2（== used）应成功
    ent = await app_seat_service.shrink_seats(db, enterprise_id=ent_id, app_id=app_id, new_seats_total=2)
    assert ent.seats_total == 2
