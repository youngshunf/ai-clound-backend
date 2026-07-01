"""P2 企业席位购买/指派 端到端切面 真实 PostgreSQL 测试（零 mock）。

覆盖（实施清单 §P2 验收 + 设计 §6.4③/§6.5）：
- settle_app_seat_purchase 回调包装：解析订单 extra_data → 累加 seats_total（首购 + 扩容）
- 回调对缺字段订单诚实返回 None（不崩、不写库）
- workbench_domain_service 席位管理四法 RBAC：非 owner/admin 一律 ForbiddenError；
  owner 可指派/列举（happy path 贯通 assign_seat + list_app_seats）

S2 幂等（每订单仅结算一次）由 ``PayOrderService.handle_pay_notify`` 的
``status==1`` 短路 + FOR UPDATE 锁保证，见 app_seat_purchase_callback 文档串。

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_seat_service
from backend.app.hasn.service.app_seat_purchase_callback import settle_app_seat_purchase
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

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


async def _seed_human(db: AsyncSession, *, user_id: int) -> str:
    hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'seat p2 {_uid()}'))
    await db.flush()
    return hasn_id


async def _seed_enterprise(db: AsyncSession, *, owner_user_id: int) -> int:
    ent = HasnEnterprise(name=f'席位P2企业 {_uid()}', slug=f'seatp2-{_uid()}', owner_user_id=owner_user_id)
    db.add(ent)
    await db.flush()
    return ent.id


async def _seed_member(
    db: AsyncSession, *, enterprise_id: int, role: str = 'member', approved: bool = True
) -> tuple[int, str]:
    """造一个企业成员（HasnHumans + membership），返回 (user_id, owner hasn_id)。"""
    user_id = 930_000_000 + int(_uid(), 16) % 1_000_000
    hasn_id = await _seed_human(db, user_id=user_id)
    db.add(
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=user_id,
            role=role,
            status='approved' if approved else 'pending',
        )
    )
    await db.flush()
    return user_id, hasn_id


def _fake_order(*, app_id: str | None, enterprise_id: int | None, seats: int | None) -> SimpleNamespace:
    extra: dict = {}
    if app_id is not None:
        extra['app_id'] = app_id
    if enterprise_id is not None:
        extra['enterprise_id'] = enterprise_id
    if seats is not None:
        extra['seats'] = seats
    return SimpleNamespace(
        order_no=f'seatorder-{_uid()}',
        billing_cycle='month',
        user_id=1,
        pay_amount=999,
        extra_data=extra,
    )


# ============================ 回调结算包装 ============================


async def test_callback_settle_accumulates(db: AsyncSession) -> None:
    """settle_app_seat_purchase：首购建权益 seats_total=4，扩容再 +3 → 7（同一权益行）。"""
    app_id = f'seat_{_uid()}'
    owner_uid = 931_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)

    ent1 = await settle_app_seat_purchase(db, order=_fake_order(app_id=app_id, enterprise_id=ent_id, seats=4))
    assert ent1 is not None
    assert ent1.seats_total == 4

    ent2 = await settle_app_seat_purchase(db, order=_fake_order(app_id=app_id, enterprise_id=ent_id, seats=3))
    assert ent2 is not None
    assert ent2.id == ent1.id
    assert ent2.seats_total == 7


async def test_callback_missing_fields_returns_none(db: AsyncSession) -> None:
    """缺字段订单：诚实返回 None，不抛、不写库。"""
    assert await settle_app_seat_purchase(db, order=_fake_order(app_id=None, enterprise_id=1, seats=2)) is None
    assert await settle_app_seat_purchase(db, order=_fake_order(app_id='x', enterprise_id=None, seats=2)) is None
    assert await settle_app_seat_purchase(db, order=_fake_order(app_id='x', enterprise_id=1, seats=0)) is None


# ============================ RBAC：席位管理四法 ============================


async def test_assign_seat_rbac_denies_non_admin(db: AsyncSession) -> None:
    app_id = f'seat_{_uid()}'
    owner_uid = 932_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=3, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    member_uid, member_hasn = await _seed_member(db, enterprise_id=ent_id, role='member')

    # 普通成员指派 → 拒
    with pytest.raises(errors.ForbiddenError):
        await workbench_domain_service.assign_app_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member_hasn, operator_user_id=member_uid
        )
    # 局外人 release → 拒
    with pytest.raises(errors.ForbiddenError):
        await workbench_domain_service.release_app_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member_hasn, operator_user_id=member_uid
        )
    # 局外人 list → 拒
    with pytest.raises(errors.ForbiddenError):
        await workbench_domain_service.list_app_seats(
            db, enterprise_id=ent_id, app_id=app_id, operator_user_id=member_uid
        )
    # 局外人 purchase → 拒（RBAC 早于下单，不触碰支付渠道）
    with pytest.raises(errors.ForbiddenError):
        await workbench_domain_service.purchase_app_seats(
            db,
            enterprise_id=ent_id,
            app_id=app_id,
            seats=2,
            billing_cycle='month',
            channel_code='wx_native',
            operator_user_id=member_uid,
        )


async def test_owner_can_assign_and_list(db: AsyncSession) -> None:
    """owner 作 operator：指派成功 + list_app_seats 回显 seats_total/seats_used/成员。"""
    app_id = f'seat_{_uid()}'
    owner_uid = 933_000_000 + int(_uid(), 16) % 1_000_000
    # owner 自身也需有 HasnHumans（resolve_owner_hasn_id 用于 assigned_by）
    await _seed_human(db, user_id=owner_uid)
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=5, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    _muid, member_hasn = await _seed_member(db, enterprise_id=ent_id, role='member')

    seat = await workbench_domain_service.assign_app_seat(
        db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member_hasn, operator_user_id=owner_uid
    )
    assert seat['member_hasn_id'] == member_hasn
    assert seat['status'] == 'assigned'

    view = await workbench_domain_service.list_app_seats(
        db, enterprise_id=ent_id, app_id=app_id, operator_user_id=owner_uid
    )
    assert view['seats_total'] == 5
    assert view['seats_used'] == 1
    assert [m['member_hasn_id'] for m in view['members']] == [member_hasn]

    # owner 回收 → released True，used 归零
    released = await workbench_domain_service.release_app_seat(
        db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member_hasn, operator_user_id=owner_uid
    )
    assert released['released'] is True
    view2 = await workbench_domain_service.list_app_seats(
        db, enterprise_id=ent_id, app_id=app_id, operator_user_id=owner_uid
    )
    assert view2['seats_used'] == 0
