"""企业应用命名席位 service（指派 / 回收 / 计数 / 购买结算 / 缩容）。

设计事实源：docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6

并发安全（S4）：assign_seat / settle_seat_purchase / shrink_seats 都在**同一事务**内先对
entitlement「套餐」行 ``SELECT ... FOR UPDATE`` 加锁，再 count/校验/写——``uq_app_seat_active``
只挡「同一成员重复指派」，不挡总量溢出，DB 层也无 ``used <= total`` CHECK，故靠 service 兜。

业务逻辑手写（对齐同域 app_catalog_service.grant_entitlement/revoke_entitlement 的先例）：
席位判定含 FOR UPDATE 并发控制，非 codegen CRUD 能表达。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_app_seat import HasnAppSeat
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 满席指派的机器可识别标记（webui/E2E 用；msg 同时带人类可读文案）。
SEATS_EXHAUSTED = 'seats_exhausted'
MUST_RELEASE_FIRST = 'must_release_seats_first'


async def count_seats_used(db: AsyncSession, *, entitlement_id: int) -> int:
    """该企业权益「套餐」行下已指派（status='assigned'）的席位数。"""
    stmt = (
        sa
        .select(sa.func.count())
        .select_from(HasnAppSeat)
        .where(HasnAppSeat.entitlement_id == entitlement_id, HasnAppSeat.status == 'assigned')
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def _lock_enterprise_entitlement(
    db: AsyncSession, *, app_id: str, enterprise_id: int
) -> HasnAppEntitlement | None:
    """取该企业对该 app 的 active 权益行并 ``FOR UPDATE`` 加锁（席位判定/结算的并发闸）。"""
    now = timezone.now()
    stmt = (
        sa
        .select(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.app_id == app_id,
            HasnAppEntitlement.subject_type == 'enterprise',
            HasnAppEntitlement.subject_id == str(enterprise_id),
            HasnAppEntitlement.status == 'active',
            sa.or_(HasnAppEntitlement.expires_at.is_(None), HasnAppEntitlement.expires_at > now),
        )
        .with_for_update()
    )
    return (await db.execute(stmt)).scalars().first()


async def _resolve_member_user_id(db: AsyncSession, *, member_hasn_id: str) -> int | None:
    """成员 owner hasn_id → sys_user.id（经 HasnHumans）。"""
    stmt = sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == member_hasn_id)
    return (await db.execute(stmt)).scalars().first()


async def _is_enterprise_member(db: AsyncSession, *, enterprise_id: int, user_id: int) -> bool:
    """该 user 是否为该企业 approved 成员。"""
    stmt = sa.select(HasnEnterpriseMembership.id).where(
        HasnEnterpriseMembership.enterprise_id == enterprise_id,
        HasnEnterpriseMembership.user_id == user_id,
        HasnEnterpriseMembership.status == 'approved',
    )
    return (await db.execute(stmt)).scalars().first() is not None


async def _member_active_seat(
    db: AsyncSession, *, enterprise_id: int, app_id: str, member_hasn_id: str
) -> HasnAppSeat | None:
    stmt = sa.select(HasnAppSeat).where(
        HasnAppSeat.enterprise_id == enterprise_id,
        HasnAppSeat.app_id == app_id,
        HasnAppSeat.member_hasn_id == member_hasn_id,
        HasnAppSeat.status == 'assigned',
    )
    return (await db.execute(stmt)).scalars().first()


async def assign_seat(
    db: AsyncSession, *, enterprise_id: int, app_id: str, member_hasn_id: str, assigned_by: str
) -> HasnAppSeat:
    """给成员指派一个席位（§6.2）。

    同一事务内：锁 entitlement 行 → 校验成员在册 → 校验未重复 → count(assigned) < seats_total → insert。
    满则抛 ``seats_exhausted``（转 4xx）。
    """
    ent = await _lock_enterprise_entitlement(db, app_id=app_id, enterprise_id=enterprise_id)
    if ent is None:
        raise errors.RequestError(msg='企业未购买该应用，无法分配席位')
    if ent.seats_total is None:
        # 非席位制权益（免费/订阅企业应用不该走席位指派，S1）
        raise errors.RequestError(msg='该应用不是席位制，无需分配席位')

    member_user_id = await _resolve_member_user_id(db, member_hasn_id=member_hasn_id)
    if member_user_id is None or not await _is_enterprise_member(
        db, enterprise_id=enterprise_id, user_id=member_user_id
    ):
        raise errors.RequestError(msg='该成员不在企业名册中')

    if (
        await _member_active_seat(db, enterprise_id=enterprise_id, app_id=app_id, member_hasn_id=member_hasn_id)
        is not None
    ):
        raise errors.RequestError(msg='该成员已分配席位')

    used = await count_seats_used(db, entitlement_id=ent.id)
    if used >= int(ent.seats_total):
        raise errors.RequestError(msg=f'席位已满（{SEATS_EXHAUSTED}），请扩容或回收席位后再指派')

    seat = HasnAppSeat(
        entitlement_id=ent.id,
        enterprise_id=enterprise_id,
        app_id=app_id,
        member_hasn_id=member_hasn_id,
        assigned_by=assigned_by,
        status='assigned',
    )
    db.add(seat)
    await db.flush()
    return seat


async def release_seat(db: AsyncSession, *, enterprise_id: int, app_id: str, member_hasn_id: str) -> bool:
    """回收成员的席位（§6.2）。幂等：无 assigned 席位返回 False，不报错。"""
    result = await db.execute(
        sa
        .update(HasnAppSeat)
        .where(
            HasnAppSeat.enterprise_id == enterprise_id,
            HasnAppSeat.app_id == app_id,
            HasnAppSeat.member_hasn_id == member_hasn_id,
            HasnAppSeat.status == 'assigned',
        )
        .values(status='released', released_at=timezone.now(), updated_time=timezone.now())
    )
    return (result.rowcount or 0) > 0


async def release_all_seats_for_member(db: AsyncSession, *, enterprise_id: int, member_hasn_id: str) -> int:
    """释放某成员在该企业**所有应用**的 assigned 席位（P4 成员退出/移除/企业解散用）。返回释放条数。"""
    result = await db.execute(
        sa
        .update(HasnAppSeat)
        .where(
            HasnAppSeat.enterprise_id == enterprise_id,
            HasnAppSeat.member_hasn_id == member_hasn_id,
            HasnAppSeat.status == 'assigned',
        )
        .values(status='released', released_at=timezone.now(), updated_time=timezone.now())
    )
    return int(result.rowcount or 0)


async def release_all_seats_for_enterprise(db: AsyncSession, *, enterprise_id: int) -> int:
    """释放该企业**所有应用所有成员**的 assigned 席位（P4 企业解散用）。返回释放条数。

    企业解散无需逐成员 ``sys_user.id→hasn_id`` 翻译（M3），按 enterprise_id 整批释放更省。
    """
    result = await db.execute(
        sa
        .update(HasnAppSeat)
        .where(
            HasnAppSeat.enterprise_id == enterprise_id,
            HasnAppSeat.status == 'assigned',
        )
        .values(status='released', released_at=timezone.now(), updated_time=timezone.now())
    )
    return int(result.rowcount or 0)


async def revoke_enterprise_entitlements(db: AsyncSession, *, enterprise_id: int) -> int:
    """吊销该企业**所有** active 应用权益「套餐」行（P4 企业解散用）。返回吊销条数。"""
    result = await db.execute(
        sa
        .update(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.subject_type == 'enterprise',
            HasnAppEntitlement.subject_id == str(enterprise_id),
            HasnAppEntitlement.status == 'active',
        )
        .values(status='revoked', updated_time=timezone.now())
    )
    return int(result.rowcount or 0)


async def settle_seat_purchase(
    db: AsyncSession, *, enterprise_id: int, app_id: str, seats: int, billing_cycle: str | None, order_ref: str
) -> HasnAppEntitlement:
    """企业席位购买结算（§6.4③，支付回调调用）：两步落席位。

    ① grant_entitlement 仅保证一条 active「套餐」权益行（其幂等分支**不写 seats_total**）；
    ② 同一事务对 ent 行 FOR UPDATE 后 ``seats_total = COALESCE(seats_total,0) + seats``（**累加**，
       首购从 NULL/0 起、扩容在原值上加，同一路径）。

    幂等去重（order_ref）由支付回调层保证（PayOrder 只结算一次），见 app_seat_purchase_callback。
    """
    if seats <= 0:
        raise errors.RequestError(msg='购买席位数必须大于 0')
    await app_catalog_service.grant_entitlement(
        db,
        app_id=app_id,
        subject_type='enterprise',
        subject_id=str(enterprise_id),
        source='purchase',
        order_ref=order_ref,
        expires_at=app_catalog_service.purchase_expiry(billing_cycle),
    )
    ent = await _lock_enterprise_entitlement(db, app_id=app_id, enterprise_id=enterprise_id)
    if ent is None:  # grant 后必存在，防御性
        raise errors.ServerError(msg='席位结算失败：权益行缺失')
    ent.seats_total = int(ent.seats_total or 0) + int(seats)
    await db.flush()
    return ent


async def shrink_seats(
    db: AsyncSession, *, enterprise_id: int, app_id: str, new_seats_total: int
) -> HasnAppEntitlement:
    """缩容席位总数（§6.5 M4）：先校验 ``new_seats_total >= 当前 seats_used``，否则拒。"""
    if new_seats_total < 0:
        raise errors.RequestError(msg='席位总数不能为负')
    ent = await _lock_enterprise_entitlement(db, app_id=app_id, enterprise_id=enterprise_id)
    if ent is None:
        raise errors.RequestError(msg='企业未购买该应用')
    used = await count_seats_used(db, entitlement_id=ent.id)
    if new_seats_total < used:
        raise errors.RequestError(msg=f'当前已指派 {used} 席，需先回收超出席位再缩容（{MUST_RELEASE_FIRST}）')
    ent.seats_total = int(new_seats_total)
    await db.flush()
    return ent
