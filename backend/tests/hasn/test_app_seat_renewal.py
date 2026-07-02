"""到期复购 / 续期 / 购买入口守卫 / runtime 工具闸企业维度 真实 PostgreSQL 测试（零 mock）。

覆盖（doc04「续费与到期复购」修订 + 评审偏差 1/2 补修，SEAT-FIX-1/2/3）：
- D1 到期让位重建：过期 active 权益行不再撞 ``uq_app_entitlement_active``——grant 前翻 expired 让位；
- D3 席位账目归一：到期复购产生新权益行时，settle 把存留 assigned 席位 re-parent 到新行
  （老成员无缝续用且计入 count_seats_used）；复购少买 → used>total 过渡态挡新指派、不踢人；
- owner 试用行过期后购买：不撞唯一索引，且 trial 历史行保留（``_has_used_trial`` 防重开）；
- 有效期内扩容回归：同一行累加、expires_at 不变（不误触让位重建）；
- 偏差1：个人购买**真实下单入口** ``_create_app_purchase_order`` 拦 ``purchasable_by=enterprise``
  （此前 E2E 只直测守卫函数、入口没接线成假绿）；
- 偏差2：``_entitlement_denial`` owner 维度不通时叠加企业维度（企业席位成员的分身工具调用放行）。

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6.4。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from decimal import Decimal
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

from backend.app.billing.schema.pay_order import CreatePayOrderParam
from backend.app.billing.service.pay_order_service import PayOrderService
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_app_seat import HasnAppSeat
from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service, app_seat_service
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
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


async def _seed_catalog(
    db: AsyncSession, *, purchasable_by: str = 'both', scope: list[str] | None = None, trial_days: int | None = None
) -> HasnAppCatalog:
    cat = HasnAppCatalog(
        app_id=f'seat_rnw_{_uid()}',
        name=f'席位续期应用 {_uid()}',
        status='published',
        access_type='purchase',
        scope=scope if scope is not None else ['personal', 'enterprise'],
        purchasable_by=purchasable_by,
        price_amount=Decimal(99),
        price_unit='cny',
        billing_cycle='month',
        trial_days=trial_days,
    )
    db.add(cat)
    await db.flush()
    return cat


async def _seed_human(db: AsyncSession, *, user_id: int) -> str:
    hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'seat rnw {_uid()}'))
    await db.flush()
    return hasn_id


async def _seed_enterprise(db: AsyncSession, *, owner_user_id: int) -> int:
    ent = HasnEnterprise(name=f'席位续期企业 {_uid()}', slug=f'seatrnw-{_uid()}', owner_user_id=owner_user_id)
    db.add(ent)
    await db.flush()
    return ent.id


async def _seed_member(db: AsyncSession, *, enterprise_id: int) -> tuple[int, str]:
    user_id = 960_000_000 + int(_uid(), 16) % 1_000_000
    hasn_id = await _seed_human(db, user_id=user_id)
    db.add(HasnEnterpriseMembership(enterprise_id=enterprise_id, user_id=user_id, role='member', status='approved'))
    await db.flush()
    return user_id, hasn_id


async def _expire_enterprise_entitlement(db: AsyncSession, *, app_id: str, enterprise_id: int) -> int:
    """把该企业该 app 的 active 权益行 expires_at 拨到过去（模拟周期到期、sweep 未跑），返回行 id。"""
    ent = (
        await db.execute(
            sa.select(HasnAppEntitlement).where(
                HasnAppEntitlement.app_id == app_id,
                HasnAppEntitlement.subject_type == 'enterprise',
                HasnAppEntitlement.subject_id == str(enterprise_id),
                HasnAppEntitlement.status == 'active',
            )
        )
    ).scalars().one()
    ent.expires_at = timezone.now() - timedelta(days=1)
    await db.flush()
    return ent.id


# ============================ D1+D3：到期复购让位重建 + 席位 re-parent ============================


async def test_enterprise_renewal_after_expiry_relinks_seats(db: AsyncSession) -> None:
    """到期复购：不撞唯一索引；旧行翻 expired；新行 seats_total=本次购买数；存留席位 re-parent 且计数正确。"""
    cat = await _seed_catalog(db)
    owner_uid = 961_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    _m1_uid, m1 = await _seed_member(db, enterprise_id=ent_id)
    _m2_uid, m2 = await _seed_member(db, enterprise_id=ent_id)
    _m3_uid, m3 = await _seed_member(db, enterprise_id=ent_id)

    first = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=5, billing_cycle='month', order_ref=f'o1-{_uid()}'
    )
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m1, assigned_by=m1)
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m2, assigned_by=m1)

    old_row_id = await _expire_enterprise_entitlement(db, app_id=cat.app_id, enterprise_id=ent_id)
    assert old_row_id == first.id

    # 复购 3 席：D1 修复前此处 grant 插新 active 行撞 uq_app_entitlement_active 直接炸。
    renewed = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=3, billing_cycle='month', order_ref=f'o2-{_uid()}'
    )
    assert renewed.id != old_row_id
    assert renewed.status == 'active'
    assert renewed.seats_total == 3  # 新周期从本次购买重计，不叠旧周期的 5

    old_row = await db.get(HasnAppEntitlement, old_row_id)
    assert old_row is not None and old_row.status == 'expired'

    # D3：存留 assigned 席位 re-parent 到新行，计数含老成员
    seats = (
        await db.execute(
            sa.select(HasnAppSeat).where(
                HasnAppSeat.enterprise_id == ent_id,
                HasnAppSeat.app_id == cat.app_id,
                HasnAppSeat.status == 'assigned',
            )
        )
    ).scalars().all()
    assert {s.member_hasn_id for s in seats} == {m1, m2}
    assert all(s.entitlement_id == renewed.id for s in seats)
    assert await app_seat_service.count_seats_used(db, entitlement_id=renewed.id) == 2

    # 老成员无缝续用；未指派成员 need_seat_assignment
    a1 = await app_catalog_service.resolve_app_access(
        db, catalog=cat, owner_hasn_id=m1, subject_type='enterprise', subject_id=str(ent_id), member_hasn_id=m1
    )
    assert a1['allowed'] is True and a1['reason'] == 'entitled'
    a3 = await app_catalog_service.resolve_app_access(
        db, catalog=cat, owner_hasn_id=m3, subject_type='enterprise', subject_id=str(ent_id), member_hasn_id=m3
    )
    assert a3['allowed'] is False and a3['reason'] == 'need_seat_assignment'

    # 剩 1 空位可指派；再多一位则满席被挡
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m3, assigned_by=m1)
    _m4_uid, m4 = await _seed_member(db, enterprise_id=ent_id)
    with pytest.raises(errors.RequestError, match='席位已满'):
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m4, assigned_by=m1
        )


async def test_renewal_smaller_than_assigned_enters_overflow_state(db: AsyncSession) -> None:
    """复购席位数 < 存留指派数：used>total 过渡态——不踢人（老成员保准入），但新指派被挡。"""
    cat = await _seed_catalog(db)
    owner_uid = 962_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    _m1_uid, m1 = await _seed_member(db, enterprise_id=ent_id)
    _m2_uid, m2 = await _seed_member(db, enterprise_id=ent_id)

    await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=3, billing_cycle='month', order_ref=f'o1-{_uid()}'
    )
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m1, assigned_by=m1)
    await app_seat_service.assign_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m2, assigned_by=m1)
    await _expire_enterprise_entitlement(db, app_id=cat.app_id, enterprise_id=ent_id)

    renewed = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=1, billing_cycle='month', order_ref=f'o2-{_uid()}'
    )
    assert renewed.seats_total == 1
    assert await app_seat_service.count_seats_used(db, entitlement_id=renewed.id) == 2  # 过渡态 used>total

    # 老成员保准入（不自动踢）
    a1 = await app_catalog_service.resolve_app_access(
        db, catalog=cat, owner_hasn_id=m1, subject_type='enterprise', subject_id=str(ent_id), member_hasn_id=m1
    )
    assert a1['allowed'] is True

    # 新指派被挡；管理员回收一个后仍满（1 席 < 剩 1 用），再回收才可指派
    _m3_uid, m3 = await _seed_member(db, enterprise_id=ent_id)
    with pytest.raises(errors.RequestError, match='席位已满'):
        await app_seat_service.assign_seat(
            db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m3, assigned_by=m1
        )
    await app_seat_service.release_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m1)
    await app_seat_service.release_seat(db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m2)
    seat = await app_seat_service.assign_seat(
        db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=m3, assigned_by=m1
    )
    assert seat.entitlement_id == renewed.id


async def test_active_expansion_keeps_row_and_expiry(db: AsyncSession) -> None:
    """有效期内扩容（回归守卫）：同一权益行累加席位，expires_at 不变、不触发让位重建。"""
    cat = await _seed_catalog(db)
    owner_uid = 963_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)

    first = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=4, billing_cycle='month', order_ref=f'o1-{_uid()}'
    )
    expiry_before = first.expires_at
    second = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=3, billing_cycle='month', order_ref=f'o2-{_uid()}'
    )
    assert second.id == first.id
    assert second.seats_total == 7
    assert second.expires_at == expiry_before


async def test_owner_trial_expired_then_purchase(db: AsyncSession) -> None:
    """owner 试用行过期后购买：不撞唯一索引；trial 历史行保留（source 不被复写）→ 试用不可重开。"""
    cat = await _seed_catalog(db, trial_days=7)
    owner_uid = 964_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn = await _seed_human(db, user_id=owner_uid)

    trial = await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner_hasn)
    trial.expires_at = timezone.now() - timedelta(days=1)
    await db.flush()

    # D1 修复前：get_active 过滤过期 → 插新 active 行 → 撞 uq（trial 行 status 仍 active）→ 炸。
    purchased = await app_catalog_service.grant_entitlement(
        db,
        app_id=cat.app_id,
        subject_type='owner',
        subject_id=owner_hasn,
        source='purchase',
        order_ref=f'o-{_uid()}',
        expires_at=None,
    )
    assert purchased.id != trial.id
    assert purchased.status == 'active' and purchased.source == 'purchase'

    trial_row = await db.get(HasnAppEntitlement, trial.id)
    assert trial_row is not None
    assert trial_row.status == 'expired'
    assert trial_row.source == 'trial'  # 历史保留，_has_used_trial 防重开
    assert await app_catalog_service._has_used_trial(
        db, app_id=cat.app_id, subject_type='owner', subject_id=owner_hasn
    )


# ============================ 偏差1：个人购买真实下单入口拦 purchasable_by ============================


async def test_personal_purchase_entry_rejects_enterprise_only_app(db: AsyncSession) -> None:
    """purchasable_by=enterprise 的应用，个人**真实下单入口**直接拒（先前只测守卫函数是假绿）。"""
    cat = await _seed_catalog(db, purchasable_by='enterprise', scope=['enterprise'])
    owner_uid = 965_000_000 + int(_uid(), 16) % 1_000_000
    await _seed_human(db, user_id=owner_uid)

    obj = CreatePayOrderParam(order_type='app_purchase', channel_code='wx_native', app_id=cat.app_id)
    with pytest.raises(errors.RequestError, match='仅限企业购买'):
        await PayOrderService._create_app_purchase_order(
            db=db, user_id=owner_uid, obj=obj, user_ip=None, app_code='huanxing'
        )


# ============================ 偏差2：runtime 工具闸叠加企业维度 ============================


async def test_runtime_gateway_overlays_enterprise_seat(db: AsyncSession) -> None:
    """企业买了席位的成员：owner 维度 need_purchase，但激活企业空间 + 有席 → 工具闸放行。"""
    cat = await _seed_catalog(db)
    owner_uid = 966_000_000 + int(_uid(), 16) % 1_000_000
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    _m_uid, member_hasn = await _seed_member(db, enterprise_id=ent_id)
    db.add(HasnOwnerWorkbenchPref(owner_hasn_id=member_hasn, active_enterprise_id=ent_id))
    await db.flush()

    await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=cat.app_id, seats=2, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    agent = SimpleNamespace(owner_hasn_id=member_hasn)

    # 企业买了但没席 → 仍拒
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=cat.app_id, agent=agent)
    assert denial == 'entitlement_denied'

    # 指派席位 → 放行
    await app_seat_service.assign_seat(
        db, enterprise_id=ent_id, app_id=cat.app_id, member_hasn_id=member_hasn, assigned_by=member_hasn
    )
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=cat.app_id, agent=agent)
    assert denial is None

    # 退到个人空间（清激活企业指针）→ 企业维度不叠加 → 拒
    pref = (
        await db.execute(
            sa.select(HasnOwnerWorkbenchPref).where(HasnOwnerWorkbenchPref.owner_hasn_id == member_hasn)
        )
    ).scalars().one()
    pref.active_enterprise_id = None
    await db.flush()
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=cat.app_id, agent=agent)
    assert denial == 'entitlement_denied'
