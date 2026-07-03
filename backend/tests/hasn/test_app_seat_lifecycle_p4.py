"""P4 席位生命周期钩子 真实 PostgreSQL 测试（零 mock）。

覆盖（实施清单 §P4 验收 + 设计 §6.5）：
- 成员退出/移除（remove_member）→ 其席位 released（M3：sys_user.id→owner_hasn_id 翻译后匹配），
  seats_used 递减，其他成员席位不受影响；
- 企业解散（delete_enterprise）→ 全部席位 released + 全部企业权益 revoked；
- release_all_seats_for_enterprise / revoke_enterprise_entitlements 幂等。

M3 关键：钩子载荷是 sys_user.id，seat 键是 hasn_id——release 前必须经 HasnHumans 翻译，
否则匹配不到席位、静默不释放（本测试的 remove_member 路径正是验证这条翻译生效）。

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6.5。
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
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
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


async def _seed_enterprise(db: AsyncSession, *, owner_user_id: int = 0) -> int:
    ent = HasnEnterprise(name=f'席位P4企业 {_uid()}', slug=f'seatp4-{_uid()}', owner_user_id=owner_user_id)
    db.add(ent)
    await db.flush()
    return ent.id


async def _seed_member(db: AsyncSession, *, enterprise_id: int, role: str = 'member') -> tuple[int, str]:
    user_id = 940_000_000 + int(_uid(), 16) % 1_000_000
    hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'seat p4 {_uid()}'))
    db.add(
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=user_id,
            role=role,
            status='approved',
        )
    )
    await db.flush()
    return user_id, hasn_id


async def _seed_entitlement(
    db: AsyncSession, *, app_id: str, enterprise_id: int, seats_total: int
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


# ============================ 成员退出释放席位（M3 翻译） ============================


async def test_remove_member_releases_seats(db: AsyncSession) -> None:
    """成员移除 → 其席位 released（经 M3 翻译匹配），其他成员不受影响。"""
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    entitlement = await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=3)
    u1, h1 = await _seed_member(db, enterprise_id=ent_id)
    _u2, h2 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=h1, assigned_by='admin')
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=h2, assigned_by='admin')
    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 2

    # 移除 u1（sys_user.id）→ 服务内 M3 翻译到 h1 → 释放其席位
    await workbench_domain_service.remove_member(db, enterprise_id=ent_id, user_id=u1)

    assert await app_seat_service.count_seats_used(db, entitlement_id=entitlement.id) == 1
    # h1 无 assigned 席位，h2 仍在
    assert (
        await app_seat_service._member_active_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=h1) is None
    )
    assert (
        await app_seat_service._member_active_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=h2)
        is not None
    )


# ============================ 企业解散释放全部 + 吊销权益 ============================


async def test_disband_releases_all_and_revokes(db: AsyncSession) -> None:
    app_a = f'seat_{_uid()}'
    app_b = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    ent_a = await _seed_entitlement(db, app_id=app_a, enterprise_id=ent_id, seats_total=2)
    ent_b = await _seed_entitlement(db, app_id=app_b, enterprise_id=ent_id, seats_total=2)
    _u1, h1 = await _seed_member(db, enterprise_id=ent_id)
    _u2, h2 = await _seed_member(db, enterprise_id=ent_id)
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_a, member_hasn_id=h1, assigned_by='admin')
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_b, member_hasn_id=h2, assigned_by='admin')

    await workbench_domain_service.delete_enterprise(db, enterprise_id=ent_id)

    # 全部席位释放
    assert await app_seat_service.count_seats_used(db, entitlement_id=ent_a.id) == 0
    assert await app_seat_service.count_seats_used(db, entitlement_id=ent_b.id) == 0
    # 全部权益 revoked
    await db.refresh(ent_a)
    await db.refresh(ent_b)
    assert ent_a.status == 'revoked'
    assert ent_b.status == 'revoked'


async def test_enterprise_bulk_helpers_idempotent(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    ent_id = await _seed_enterprise(db)
    await _seed_entitlement(db, app_id=app_id, enterprise_id=ent_id, seats_total=2)
    _u1, h1 = await _seed_member(db, enterprise_id=ent_id)
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=h1, assigned_by='admin')

    assert await app_seat_service.release_all_seats_for_enterprise(db, enterprise_id=ent_id) == 1
    # 再释放一次 → 0（幂等）
    assert await app_seat_service.release_all_seats_for_enterprise(db, enterprise_id=ent_id) == 0
    assert await app_seat_service.revoke_enterprise_entitlements(db, enterprise_id=ent_id) == 1
    assert await app_seat_service.revoke_enterprise_entitlements(db, enterprise_id=ent_id) == 0
