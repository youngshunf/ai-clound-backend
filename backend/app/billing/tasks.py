"""定时任务：API Key 过期检查 + 统一商业化生命周期 sweeper（MK-5）

积分相关的两支任务（年付发积分、每小时积分对账）已在 doc94 P0 退役：
NewAPI 是积分余额、用量、扣减与清零的唯一权威，云端不再发放余额、也不再覆盖写回 quota。
@author Ysf
"""

from __future__ import annotations

import math

from datetime import timedelta
from typing import TYPE_CHECKING

from celery import shared_task
from sqlalchemy import and_, select, update

from backend.app.billing.model import UserSubscription
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
    """已退役（doc94 P0）：年付订阅的周期额度改由 NewAPI 按 30 天固定周期清零重置。

    原实现在云端直接写 UserCreditBalance / CreditTransaction 发积分，属于「云端算余额」的
    反向数据流。云端已彻底退出积分余额与发放，本任务只保留一个显式的退役返回，
    以免存量调度表或人工触发时以为它还在正常工作。调度表条目已移除，
    并由 `assert_no_retired_credit_tasks` 在启动期守住。
    """
    log.warning('[YearlyGrant] 任务已退役：年付周期额度由 NewAPI 按 30 天固定周期清零重置，云端不再发放积分')
    return '年度订阅积分发放任务已退役（NewAPI 为积分唯一权威）'


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
    """已退役（doc94 P0）：云端不再与 NewAPI 对账余额，更不会覆盖写回 quota。

    原实现按「NewAPI 已用量 + 云端剩余额度」算出目标 quota 覆盖写回，导致 NewAPI 刚扣掉的
    额度在下一轮同步又被云端写回，余额不足门禁被反向充值抵消（这正是「超额仍可用」的根因）。
    余额、用量、扣减与清零现在只有 NewAPI 一个权威，云端只读不写。
    """
    log.warning('[CreditSync] 任务已退役：NewAPI 是积分唯一权威，云端不再回扣账本或重设 quota')
    return '积分账本每小时对账任务已退役（NewAPI 为积分唯一权威）'


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


# ==================== doc94 C2：履约 outbox worker ====================


@shared_task(name='credit_outbox_dispatch')
async def credit_outbox_dispatch() -> str:
    """投递待履约命令到 NewAPI（每分钟一轮）。

    云端事务里只写命令，真正碰 NewAPI 的动作在这里。抢占用 FOR UPDATE SKIP LOCKED，
    多副本并发跑同一张表互不重复也不阻塞；超时只用同一个 event_id 对账重投，绝不换 ID 重发。
    """
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    summary = await credit_outbox_service.run_once()
    result_msg = (
        f'履约投递完成: 抢占 {summary["claimed"]} 条, 成功 {summary["succeeded"]}, '
        f'重试 {summary["retrying"]}, 死信 {summary["dead"]}'
    )
    if summary['claimed']:
        log.info(f'[CreditOutbox] {result_msg}')
    return result_msg


@shared_task(name='credit_outbox_reconcile')
async def credit_outbox_reconcile() -> str:
    """履约对账：只核对「云端事件是否在 NewAPI 有对应结果」，缺失就重放事件。

    绝不根据云端数据重算或覆盖 NewAPI 余额——那正是本轮要消灭的反向数据流。
    """
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    summary = await credit_outbox_service.reconcile_missing_projections()
    result_msg = f'履约对账完成: 核对 {summary["checked"]} 条, 收敛 {summary["reconciled"]} 条'
    log.info(f'[CreditOutbox] {result_msg}')
    return result_msg


@shared_task(name='credit_outbox_metrics_refresh')
async def credit_outbox_metrics_refresh() -> str:
    """刷新履约相关的 Gauge 指标（已支付未履约、outbox 积压与最老等待时长）。"""
    from backend.app.billing.observability.metrics import (
        BILLING_PAID_UNFULFILLED_TOTAL,
        CREDIT_OUTBOX_OLDEST_AGE_SECONDS,
        CREDIT_OUTBOX_PENDING_TOTAL,
    )
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    metrics = await credit_outbox_service.collect_metrics()
    CREDIT_OUTBOX_PENDING_TOTAL.set(metrics['credit_outbox_pending_total'])
    CREDIT_OUTBOX_OLDEST_AGE_SECONDS.set(metrics['credit_outbox_oldest_age_seconds'])
    BILLING_PAID_UNFULFILLED_TOTAL.set(metrics['billing_paid_unfulfilled_total'])
    return f'履约指标已刷新: {metrics}'
