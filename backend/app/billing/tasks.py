"""定时任务：年度订阅积分发放 + API Key 过期检查 + 统一商业化生命周期 sweeper（MK-5）
@author Ysf
"""

from __future__ import annotations

import math

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from celery import shared_task
from sqlalchemy import and_, select, update

from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.model import CreditTransaction, UserCreditBalance, UserSubscription
from backend.app.newapi.apikey.enums import ApiKeyStatus
from backend.app.newapi.apikey.model import UserApiKey
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


@shared_task(name='grant_yearly_subscription_credits')
async def grant_yearly_subscription_credits() -> str:
    """
    年度订阅用户每月积分发放任务

    每天凌晨执行，检查符合条件的年度订阅用户：
    - subscription_type = 'yearly'
    - status = 'active'
    - next_grant_date <= now
    - subscription_end_date > now (订阅未过期)

    为符合条件的用户：
    1. 创建积分余额记录
    2. 更新 next_grant_date 为下个月
    3. 记录交易
    """
    now = timezone.now()
    granted_count = 0
    error_count = 0

    async with async_db_session.begin() as db:
        # 查询需要发放积分的年度订阅用户
        stmt = select(UserSubscription).where(
            and_(
                UserSubscription.subscription_type == 'yearly',
                UserSubscription.status == 'active',
                UserSubscription.next_grant_date <= now,
                UserSubscription.subscription_end_date > now,
            )
        )
        result = await db.execute(stmt)
        subscriptions = result.scalars().all()

        log.info(f'[YearlyGrant] 找到 {len(subscriptions)} 个需要发放积分的年度订阅用户')

        for subscription in subscriptions:
            try:
                # 获取等级配置
                tier = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier)
                if not tier:
                    log.warning(f'[YearlyGrant] 用户 {subscription.user_id} 的订阅等级 {subscription.tier} 不存在')
                    error_count += 1
                    continue

                monthly_credits = tier.monthly_credits

                # 计算下次发放时间和积分有效期
                next_grant = subscription.next_grant_date + timedelta(days=30)
                cycle_end = subscription.next_grant_date + timedelta(days=30)

                # 确保不超过订阅结束时间
                if next_grant > subscription.subscription_end_date:
                    next_grant = None  # 最后一次发放，不再设置下次发放时间

                # 计算发放的月份数（从订阅开始算起）
                if subscription.subscription_start_date:
                    months_elapsed = (
                        subscription.next_grant_date - subscription.subscription_start_date
                    ).days // 30 + 1
                else:
                    months_elapsed = 1

                # 创建积分余额记录
                balance = UserCreditBalance(
                    user_id=subscription.user_id,
                    credit_type='monthly',
                    original_amount=monthly_credits,
                    used_amount=Decimal(0),
                    remaining_amount=monthly_credits,
                    expires_at=cycle_end,
                    granted_at=now,
                    source_type='yearly_subscription_grant',
                    description=f'年度订阅: {subscription.tier} (第{months_elapsed}个月)',
                )
                db.add(balance)

                # 获取当前总积分（用于记录交易）
                from sqlalchemy import func

                from backend.app.billing.model import UserCreditBalance as UCB

                balance_stmt = select(func.coalesce(func.sum(UCB.remaining_amount), 0)).where(
                    and_(
                        UCB.user_id == subscription.user_id,
                        UCB.remaining_amount > 0,
                    )
                )
                balance_result = await db.execute(balance_stmt)
                current_balance = Decimal(str(balance_result.scalar() or 0))

                # 记录交易
                transaction = CreditTransaction(
                    user_id=subscription.user_id,
                    transaction_type='yearly_grant',
                    credits=monthly_credits,
                    balance_before=current_balance,
                    balance_after=current_balance + monthly_credits,
                    description=f'年度订阅月度赠送: {subscription.tier} (第{months_elapsed}个月)',
                    extra_data={
                        'tier': subscription.tier,
                        'month': months_elapsed,
                        'subscription_type': 'yearly',
                    },
                )
                db.add(transaction)

                # 更新订阅的下次发放时间和计费周期
                subscription.next_grant_date = next_grant
                subscription.billing_cycle_start = now
                subscription.billing_cycle_end = cycle_end
                subscription.current_credits = current_balance + monthly_credits

                granted_count += 1
                log.info(
                    f'[YearlyGrant] 用户 {subscription.user_id} 发放 {monthly_credits} 积分成功 (第{months_elapsed}个月)'
                )

            except Exception as e:
                log.error(f'[YearlyGrant] 用户 {subscription.user_id} 发放积分失败: {e}')
                error_count += 1
                continue

    result_msg = f'年度订阅积分发放完成: 成功 {granted_count} 个, 失败 {error_count} 个'
    log.info(f'[YearlyGrant] {result_msg}')
    return result_msg


@shared_task(name='check_expired_api_keys')
async def check_expired_api_keys() -> str:
    """
    每日检查并标记过期的 API Key

    查找所有状态为 ACTIVE 但 expires_at < now 的 Key，
    将其状态更新为 EXPIRED。
    """
    now = timezone.now()

    async with async_db_session.begin() as db:
        # 批量更新过期的 API Key
        stmt = (
            update(UserApiKey)
            .where(
                and_(
                    UserApiKey.status == ApiKeyStatus.ACTIVE,
                    UserApiKey.expires_at.isnot(None),
                    UserApiKey.expires_at < now,
                )
            )
            .values(status=ApiKeyStatus.EXPIRED)
        )
        result = await db.execute(stmt)
        expired_count = result.rowcount

    result_msg = f'API Key 过期检查完成: {expired_count} 个 Key 已标记为过期'
    log.info(f'[ExpiredKeyCheck] {result_msg}')
    return result_msg


@shared_task(name='newapi_hourly_credit_sync')
async def newapi_hourly_credit_sync() -> str:
    """§5A.5 每小时积分账本对账：把 new-api 真实消费增量回扣账本 + 重设 quota = 账本剩余。

    遍历所有 active new-api 映射用户，逐用户独立事务对账（单用户失败不影响其余，
    new-api 不可达则跳过该用户、下轮重试，绝不臆造扣减——零 fake）。
    """
    from backend.app.newapi.credit_sync_service import credit_sync_service

    summary = await credit_sync_service.reconcile_all()
    result_msg = (
        f'积分账本每小时对账完成: 共 {summary["total"]} 户, '
        f'有消费 {summary["ok"]} / 无变化 {summary["no_delta"]} / 跳过 {summary["skipped"]} / 失败 {summary["failed"]}, '
        f'合计回扣 {summary["consumed_credits_total"]} 积分'
    )
    log.info(f'[CreditSync] {result_msg}')
    return result_msg


@shared_task(name='expire_overdue_subscriptions')
async def expire_overdue_subscriptions() -> str:
    """每日检查并标记过期订阅（收敛存量 status）。

    付费订阅（subscription_end_date 非空且 < now）若仍为 active → 置 expired。
    免费版 subscription_end_date 为 NULL，不参与（永不过期）。
    与 /info 读取时的状态重算（credit_service.get_user_credits_info）口径一致——读路径已
    实时纠正显示，本任务保证 DB 存量 status 也收敛，供 admin 列表等其它读路径正确。
    """
    now = timezone.now()

    async with async_db_session.begin() as db:
        stmt = (
            update(UserSubscription)
            .where(
                and_(
                    UserSubscription.status == 'active',
                    UserSubscription.tier != 'free',
                    UserSubscription.subscription_end_date.isnot(None),
                    UserSubscription.subscription_end_date < now,
                )
            )
            .values(status='expired')
        )
        result = await db.execute(stmt)
        expired_count = result.rowcount

    result_msg = f'订阅过期检查完成: {expired_count} 个订阅已标记为过期'
    log.info(f'[ExpiredSubscription] {result_msg}')
    return result_msg


# ==================== MK-5：统一商业化生命周期 sweeper ====================

# 到期前提醒阈值（天）：到期前 7/3/1 天各提醒一次（统一通知系统 emit·dedupe_key 去重）。
# sweep 每日跑一次、到期时间固定，剩余天数逐日递减，故每个阈值恰好命中一次、不重不漏。
_EXPIRY_REMINDER_DAYS = (7, 3, 1)


def _days_until(when: datetime, now: datetime) -> int:
    """剩余到期天数（向上取整）：还剩不足 1 天算 1 天；已过期返回 0。"""
    seconds = (when - now).total_seconds()
    if seconds <= 0:
        return 0
    return math.ceil(seconds / 86400)


async def _owner_hasn_for_user(db: AsyncSession, user_id: int) -> str | None:
    """user_id → owner hasn_id（订阅按 user_id 归属，通知/WSPUSH 按 hasn_id 定向）。"""
    from backend.app.hasn.model.hasn_humans import HasnHumans

    return (await db.execute(select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalars().first()


async def _emit_expiry_reminder(
    db: AsyncSession, *, recipient_id: str, billing_kind: str, ref_id: str, label: str, days_left: int
) -> None:
    """发一条到期提醒（commerce 类·统一通知系统 emit）。dedupe_key 含 days_left → 同阈值只发一次。"""
    from backend.app.notification.service.notification_service import NotificationService

    if billing_kind == 'subscription':
        title = f'订阅将在 {days_left} 天后到期'
        body = f'你的「{label}」订阅将在 {days_left} 天后到期，续订以免服务与配额中断。'
    else:
        title = f'应用权益将在 {days_left} 天后到期'
        body = f'你的应用权益「{label}」将在 {days_left} 天后到期，续订以免相关能力停用。'
    await NotificationService.emit(
        db,
        recipient_id=recipient_id,
        source={'kind': 'system', 'id': 'billing'},
        category='commerce',
        type='billing_expiry',
        title=title,
        body=body,
        payload={
            'billing_kind': billing_kind,
            'ref_id': ref_id,
            'label': label,
            'days_left': days_left,
            'target': {'id': ref_id},
            # 费用账单中心深链（客户端无关 hasn:// URI·MK-8 承载渲染）
            'primary_action': {'uri': 'hasn://billing/center'},
        },
        dedupe_key=f'billing_expiry:{billing_kind}:{ref_id}:{days_left}',
    )


async def _bump_owner_billing_safe(db: AsyncSession, owner_hasn: str) -> None:
    """给单个 owner 发 billing 变更事件（WSPUSH KIND_BILLING）。best-effort：单个失败不拖垮整批。

    错误隔离收在本 helper 内（而非 sweep 循环体内的 try/except），避免 PERF203。
    """
    from backend.app.hasn.service import sync_invalidate_service

    try:
        await sync_invalidate_service.bump_owner(sync_invalidate_service.KIND_BILLING, db, owner_hasn)
    except Exception as e:
        log.warning(f'[BillingSweep] bump_owner billing 失败 owner={owner_hasn}: {e}')


async def _emit_due_reminders(db: AsyncSession, now: datetime, horizon: datetime) -> int:
    """Phase 1：到期前 7/3/1 天各发一次提醒（权益 + 付费订阅）。返回提醒条数。

    抽出为独立 helper 让 run_billing_lifecycle_sweep 复杂度低于阈值（C901）。
    """
    from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement

    reminded = 0
    # 权益提醒（subject_type=owner，subject_id 即 owner hasn_id）
    ent_soon = (
        (
            await db.execute(
                select(HasnAppEntitlement).where(
                    HasnAppEntitlement.status == 'active',
                    HasnAppEntitlement.subject_type == 'owner',
                    HasnAppEntitlement.expires_at.is_not(None),
                    HasnAppEntitlement.expires_at > now,
                    HasnAppEntitlement.expires_at <= horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    for ent in ent_soon:
        days_left = _days_until(ent.expires_at, now)
        if days_left in _EXPIRY_REMINDER_DAYS and ent.subject_id:
            await _emit_expiry_reminder(
                db,
                recipient_id=ent.subject_id,
                billing_kind='entitlement',
                ref_id=str(ent.id),
                label=ent.app_id,
                days_left=days_left,
            )
            reminded += 1

    # 付费订阅提醒（tier != free，user_id → hasn_id）
    sub_soon = (
        (
            await db.execute(
                select(UserSubscription).where(
                    UserSubscription.status == 'active',
                    UserSubscription.tier != 'free',
                    UserSubscription.subscription_end_date.is_not(None),
                    UserSubscription.subscription_end_date > now,
                    UserSubscription.subscription_end_date <= horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    for sub in sub_soon:
        days_left = _days_until(sub.subscription_end_date, now)
        if days_left in _EXPIRY_REMINDER_DAYS:
            owner_hasn = await _owner_hasn_for_user(db, sub.user_id)
            if owner_hasn:
                await _emit_expiry_reminder(
                    db,
                    recipient_id=owner_hasn,
                    billing_kind='subscription',
                    ref_id=str(sub.id),
                    label=sub.tier,
                    days_left=days_left,
                )
                reminded += 1
    return reminded


async def run_billing_lifecycle_sweep() -> dict[str, int]:
    """统一商业化生命周期 sweeper 核心（实施/92 MK-5）——收编权益过期兜底 + 订阅过期兜底 + 到期提醒。

    单入口按权威到期时间执行「提醒(7/3/1天)→到期」；宽限（expired_in_grace）与终态是
    resolve_access 读时按 plan.grace_json.grace_days 计算的派生态、不落库，本 sweeper 只负责：
    1. 提醒：权益/付费订阅在到期前 7/3/1 天，经统一通知系统 emit(commerce, dedupe_key) 各提醒一次；
    2. 到期：已过期的 active 权益/付费订阅置 expired（收编 sweep_expired_entitlements +
       expire_overdue_subscriptions 两支旧任务的动作·旧任务双跑一期后于 MK-9 摘除）；
    3. 变更事件：到期涉及的每个 owner bump_owner(KIND_BILLING)，daemon 拉最新账单中心镜像。

    零 fake：只据权威到期时间行事；免费版（subscription_end_date=NULL）永不过期、不参与。
    返回计数字典（供 celery 任务包装成日志文案·供 pytest 断言状态机）。
    """
    from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement

    now = timezone.now()
    horizon = now + timedelta(days=max(_EXPIRY_REMINDER_DAYS))
    reminded = 0
    expired_ent = 0
    expired_sub = 0
    affected_owners: set[str] = set()

    async with async_db_session.begin() as db:
        # ---- 1. 提醒：到期前 7/3/1 天（权益 + 付费订阅，抽出 helper 控复杂度）----
        reminded = await _emit_due_reminders(db, now, horizon)

        # ---- 2. 到期：权益 active → expired（收编 sweep_expired_entitlements）----
        ent_overdue = (
            await db.execute(
                select(HasnAppEntitlement.id, HasnAppEntitlement.subject_id).where(
                    HasnAppEntitlement.status == 'active',
                    HasnAppEntitlement.subject_type == 'owner',
                    HasnAppEntitlement.expires_at.is_not(None),
                    HasnAppEntitlement.expires_at < now,
                )
            )
        ).all()
        if ent_overdue:
            ent_ids = [row_id for row_id, _ in ent_overdue]
            await db.execute(
                update(HasnAppEntitlement)
                .where(HasnAppEntitlement.id.in_(ent_ids))
                .values(status='expired', updated_time=now)
            )
            expired_ent = len(ent_ids)
            affected_owners.update(sid for _, sid in ent_overdue if sid)

        # ---- 2. 到期：付费订阅 active → expired（收编 expire_overdue_subscriptions）----
        sub_overdue = (
            await db.execute(
                select(UserSubscription.id, UserSubscription.user_id).where(
                    UserSubscription.status == 'active',
                    UserSubscription.tier != 'free',
                    UserSubscription.subscription_end_date.is_not(None),
                    UserSubscription.subscription_end_date < now,
                )
            )
        ).all()
        if sub_overdue:
            sub_ids = [row_id for row_id, _ in sub_overdue]
            await db.execute(update(UserSubscription).where(UserSubscription.id.in_(sub_ids)).values(status='expired'))
            expired_sub = len(sub_ids)
            for _, uid in sub_overdue:
                owner_hasn = await _owner_hasn_for_user(db, uid)
                if owner_hasn:
                    affected_owners.add(owner_hasn)

    # ---- 3. 变更事件：WSPUSH billing kind（提交后新会话读到已 expired 的权威态）----
    if affected_owners:
        async with async_db_session() as db:
            for owner_hasn in affected_owners:
                await _bump_owner_billing_safe(db, owner_hasn)

    return {
        'reminded': reminded,
        'expired_ent': expired_ent,
        'expired_sub': expired_sub,
        'affected_owners': len(affected_owners),
    }


@shared_task(name='billing_lifecycle_sweep')
async def billing_lifecycle_sweep() -> str:
    """celery 任务包装（beat 单入口·MK-5）：跑核心 sweep + 落日志。"""
    r = await run_billing_lifecycle_sweep()
    result_msg = (
        f'商业化生命周期 sweep 完成: 提醒 {r["reminded"]} 条, '
        f'权益到期 {r["expired_ent"]} 个, 订阅到期 {r["expired_sub"]} 个, '
        f'变更 owner {r["affected_owners"]} 个'
    )
    log.info(f'[BillingSweep] {result_msg}')
    return result_msg
