"""履约 outbox worker（doc94 C2）。

它是「云端事务里写下的命令」与「NewAPI 权威账本」之间唯一的桥。三条纪律：

1. **抢占用 FOR UPDATE SKIP LOCKED**：多个 worker 并发跑同一张表时互相跳过对方锁住的行，
   既不重复投递也不互相阻塞。
2. **超时只查不换 ID**：网络超时后无法判断 NewAPI 到底执行没执行，只能 GET 同一个 ``event_id``——
   404 表示确定没发生（可安全重投）、200+failed 表示终局失败（直接 dead）、
   200+succeeded 表示已成功（收敛）。**绝不生成新 event 重发**，那等于放弃幂等。
3. **对账只补投影不覆盖余额**：发现「云端有事件、NewAPI 无投影」只重放事件，
   绝不根据云端数据重算或覆盖 NewAPI 的余额——那正是本轮要消灭的反向数据流。
"""

from __future__ import annotations

import random

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.model.pay_refund import PayRefund
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.observability.metrics import (
    CREDIT_OUTBOX_DEAD_TOTAL,
    CREDIT_OUTBOX_RETRY_TOTAL,
    NEWAPI_CREDIT_OPERATION_FAILED_TOTAL,
    NEWAPI_CREDIT_OPERATION_IDEMPOTENCY_CONFLICT_TOTAL,
    NEWAPI_CREDIT_OPERATION_IDEMPOTENT_REPLAY_TOTAL,
)
from backend.app.billing.service.credit_grant_event_service import (
    EVENT_SUBSCRIPTION_ACTIVATE,
    EVENT_SUBSCRIPTION_EXPIRE,
    OPEN_STATUSES,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_RETRYING,
    STATUS_SUCCEEDED,
)
from backend.app.newapi.credit_client import CreditOperationOutcome, NewApiCreditError, newapi_credit_client
from backend.common.log import log
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

# 指数退避基数（秒）：1、2、4、8… 上限 15 分钟，另加 ±20% 抖动打散重试洪峰。
_BACKOFF_BASE_SECONDS = 1
_BACKOFF_MAX_SECONDS = 900


def _next_backoff_seconds(attempt_count: int) -> float:
    base = min(_BACKOFF_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)), _BACKOFF_MAX_SECONDS)
    jitter = base * random.uniform(-0.2, 0.2)  # noqa: S311 — 退避抖动不需要密码学随机
    return max(1.0, base + jitter)


class CreditOutboxService:
    """履约事件的投递、状态推进与对账。"""

    @staticmethod
    async def claim_batch(db: AsyncSession, limit: int) -> list[CreditGrantEvent]:
        """抢占一批待投递事件。

        ``FOR UPDATE SKIP LOCKED`` 让并发 worker 各取各的，不重复也不阻塞；
        抢到即置 ``processing``，避免同一事件被两个 worker 同时投递。
        """
        now = timezone.now()
        result = await db.execute(
            select(CreditGrantEvent)
            .where(
                CreditGrantEvent.status.in_((STATUS_PENDING, STATUS_RETRYING)),
                CreditGrantEvent.next_attempt_at <= now,
            )
            .order_by(CreditGrantEvent.next_attempt_at.asc(), CreditGrantEvent.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars().all())
        for event in events:
            event.status = STATUS_PROCESSING
            event.attempt_count += 1
        await db.flush()
        return events

    @staticmethod
    async def deliver_one(event_id: str) -> str:
        """投递单条事件，返回终局状态（succeeded / retrying / dead）。

        每条事件独立事务：一条失败不拖垮同批其它事件。
        """
        async with async_db_session.begin() as db:
            event = (
                await db.execute(select(CreditGrantEvent).where(CreditGrantEvent.event_id == event_id))
            ).scalar_one_or_none()
            if event is None:
                return STATUS_DEAD
            payload = dict(event.payload or {})
            attempt = event.attempt_count

        try:
            outcome = await newapi_credit_client.put_credit_operation(event_id, payload)
        except NewApiCreditError as exc:
            if exc.code == 'idempotency_conflict':
                NEWAPI_CREDIT_OPERATION_IDEMPOTENCY_CONFLICT_TOTAL.inc()
            if exc.retryable:
                # 可能是「已执行但响应丢了」，也可能是根本没执行——用同一个 event_id GET 对账。
                reconciled = await CreditOutboxService._reconcile_after_timeout(event_id)
                if reconciled is not None:
                    return await CreditOutboxService._settle(event_id, reconciled)
                return await CreditOutboxService._schedule_retry(event_id, attempt, exc.code, str(exc))
            return await CreditOutboxService._mark_dead(event_id, exc.code, str(exc))

        return await CreditOutboxService._settle(event_id, outcome)

    @staticmethod
    async def _reconcile_after_timeout(event_id: str) -> CreditOperationOutcome | None:
        """超时后对账：返回终局回执，或 ``None`` 表示「确定没发生 / 暂时查不到」。"""
        try:
            return await newapi_credit_client.get_credit_operation(event_id)
        except NewApiCreditError as exc:
            log.warning(f'[CreditOutbox] 超时对账查询失败 event_id={event_id}: {exc}')
            return None

    @staticmethod
    async def _settle(event_id: str, outcome: CreditOperationOutcome) -> str:
        """把 NewAPI 的终局回执落到事件与关联单据上。"""
        if outcome.idempotent_replay:
            NEWAPI_CREDIT_OPERATION_IDEMPOTENT_REPLAY_TOTAL.inc()

        if not outcome.succeeded:
            failure_code = outcome.failure_code or 'unknown_failure'
            NEWAPI_CREDIT_OPERATION_FAILED_TOTAL.labels(failure_code=failure_code).inc()
            return await CreditOutboxService._mark_dead(event_id, failure_code, 'NewAPI 终局业务失败')

        async with async_db_session.begin() as db:
            event = (
                await db.execute(
                    select(CreditGrantEvent).where(CreditGrantEvent.event_id == event_id).with_for_update()
                )
            ).scalar_one_or_none()
            if event is None:
                return STATUS_DEAD
            event.status = STATUS_SUCCEEDED
            event.delivered_at = timezone.now()
            event.last_error_code = None
            event.last_error_message = None
            event.response_snapshot = outcome.raw
            if outcome.applied_credits is not None:
                # 审计以 NewAPI 回执为准，不以请求值反推。
                event.applied_credits = Decimal(str(outcome.applied_credits))
            await CreditOutboxService._propagate_success(db, event)
        log.info(f'[CreditOutbox] 履约成功 event_id={event_id} applied={outcome.applied_credits}')
        await CreditOutboxService._continue_refund_saga(event_id)
        return STATUS_SUCCEEDED

    @staticmethod
    async def _propagate_success(db: AsyncSession, event: CreditGrantEvent) -> None:
        """事件成功后原子更新关联单据的履约状态。"""
        now = timezone.now()
        if event.order_no and not event.refund_no:
            await db.execute(
                update(PayOrder)
                .where(PayOrder.order_no == event.order_no)
                .values(
                    fulfillment_status='succeeded',
                    fulfilled_at=now,
                    fulfillment_error_code=None,
                    fulfillment_event_id=event.event_id,
                )
            )
        elif event.order_no:
            # 退款回收成功：订单的履约状态是「已回收」，不是「已到账」。
            # 两者共用 order_no，写错一个字用户端就会在退款后仍显示「购买完成」。
            await db.execute(
                update(PayOrder)
                .where(PayOrder.order_no == event.order_no)
                .values(fulfillment_status='reversed', fulfillment_error_code=None)
            )
        if event.refund_no:
            await db.execute(
                update(PayRefund).where(PayRefund.refund_no == event.refund_no).values(fulfillment_status='succeeded')
            )
        if event.subscription_id:
            values: dict[str, Any] = {'fulfillment_status': 'succeeded'}
            if event.event_type == EVENT_SUBSCRIPTION_ACTIVATE:
                values['status'] = 'active'
            elif event.event_type == EVENT_SUBSCRIPTION_EXPIRE:
                values['status'] = 'expired'
            await db.execute(
                update(UserSubscription).where(UserSubscription.id == event.subscription_id).values(**values)
            )

    @staticmethod
    async def _continue_refund_saga(event_id: str) -> None:
        """回收命令成功后推进退款 saga 的第二段：调支付渠道。

        顺序不能反——先回收额度、再退钱。补偿事件本身不再触发渠道调用，
        否则「补偿成功 → 又去退一次钱」会绕成死循环。
        """
        async with async_db_session() as db:
            event = (
                await db.execute(select(CreditGrantEvent).where(CreditGrantEvent.event_id == event_id))
            ).scalar_one_or_none()
            if event is None or not event.refund_no:
                return
            reason = (event.payload or {}).get('reason', '')
            event_type = event.event_type
            refund_no = event.refund_no
        if event_type not in (EVENT_SUBSCRIPTION_EXPIRE, 'wallet_revoke'):
            return
        if str(reason).startswith('channel_refund_failed'):
            return

        from backend.app.billing.service.refund_settlement_service import refund_settlement_service

        try:
            await refund_settlement_service.settle_channel_refund(refund_no)
        except Exception as exc:
            log.error(f'[CreditOutbox] 退款 saga 渠道结算异常 refund_no={refund_no}: {exc!r}')

    @staticmethod
    async def _schedule_retry(event_id: str, attempt: int, error_code: str, message: str) -> str:
        """安排下一次重投；超过阈值转 dead 并告警。"""
        if attempt >= settings.CREDIT_OUTBOX_MAX_ATTEMPTS:
            return await CreditOutboxService._mark_dead(event_id, error_code, f'重试次数耗尽: {message}')

        CREDIT_OUTBOX_RETRY_TOTAL.labels(error_code=error_code).inc()
        delay = _next_backoff_seconds(attempt)
        async with async_db_session.begin() as db:
            await db.execute(
                update(CreditGrantEvent)
                .where(CreditGrantEvent.event_id == event_id)
                .values(
                    status=STATUS_RETRYING,
                    next_attempt_at=timezone.now() + timedelta(seconds=delay),
                    last_error_code=error_code,
                    last_error_message=message[:500],
                )
            )
            await CreditOutboxService._mark_related_documents(db, event_id, 'retrying', error_code)
        log.warning(f'[CreditOutbox] 履约将重试 event_id={event_id} attempt={attempt} delay={delay:.1f}s code={error_code}')
        return STATUS_RETRYING

    @staticmethod
    async def _mark_dead(event_id: str, error_code: str, message: str) -> str:
        """终局失败进 dead letter，发 error 告警等人工介入。"""
        CREDIT_OUTBOX_DEAD_TOTAL.labels(error_code=error_code).inc()
        async with async_db_session.begin() as db:
            await db.execute(
                update(CreditGrantEvent)
                .where(CreditGrantEvent.event_id == event_id)
                .values(
                    status=STATUS_DEAD,
                    next_attempt_at=None,
                    last_error_code=error_code,
                    last_error_message=message[:500],
                )
            )
            await CreditOutboxService._mark_related_documents(db, event_id, 'dead', error_code)
        log.error(f'[CreditOutbox] 履约进入死信，需人工处置 event_id={event_id} code={error_code}: {message}')
        return STATUS_DEAD

    @staticmethod
    async def _mark_related_documents(db: AsyncSession, event_id: str, status: str, error_code: str) -> None:
        event = (
            await db.execute(select(CreditGrantEvent).where(CreditGrantEvent.event_id == event_id))
        ).scalar_one_or_none()
        if event is None:
            return
        if event.order_no:
            await db.execute(
                update(PayOrder)
                .where(PayOrder.order_no == event.order_no)
                .values(fulfillment_status=status, fulfillment_error_code=error_code, fulfillment_event_id=event.event_id)
            )
        if event.refund_no:
            await db.execute(
                update(PayRefund).where(PayRefund.refund_no == event.refund_no).values(fulfillment_status=status)
            )
        if event.subscription_id:
            await db.execute(
                update(UserSubscription)
                .where(UserSubscription.id == event.subscription_id)
                .values(fulfillment_status=status)
            )

    @staticmethod
    async def run_once() -> dict[str, int]:
        """跑一轮投递：抢占一批 → 逐条独立事务投递。"""
        limit = max(int(settings.CREDIT_OUTBOX_BATCH_SIZE), 1)
        async with async_db_session.begin() as db:
            claimed = await CreditOutboxService.claim_batch(db, limit)
            event_ids = [event.event_id for event in claimed]

        summary = {'claimed': len(event_ids), 'succeeded': 0, 'retrying': 0, 'dead': 0}
        for event_id in event_ids:
            try:
                result = await CreditOutboxService.deliver_one(event_id)
            except Exception as exc:
                log.error(f'[CreditOutbox] 投递异常 event_id={event_id}: {exc!r}')
                await CreditOutboxService._schedule_retry(event_id, 1, 'worker_exception', repr(exc))
                summary['retrying'] += 1
                continue
            if result == STATUS_SUCCEEDED:
                summary['succeeded'] += 1
            elif result == STATUS_RETRYING:
                summary['retrying'] += 1
            else:
                summary['dead'] += 1
        return summary

    @staticmethod
    async def reconcile_missing_projections(limit: int = 200) -> dict[str, int]:
        """对账：只核对「云端事件是否在 NewAPI 有对应结果」，缺失就重放事件。

        **绝不**根据云端数据重算或覆盖 NewAPI 余额——这条边界一旦破掉，
        「超额仍可用」就会原样回来。
        """
        replayed = 0
        checked = 0
        async with async_db_session() as db:
            result = await db.execute(
                select(CreditGrantEvent)
                .where(CreditGrantEvent.status == STATUS_DEAD)
                .order_by(CreditGrantEvent.updated_time.desc())
                .limit(limit)
            )
            events = list(result.scalars().all())

        for event in events:
            checked += 1
            try:
                outcome = await newapi_credit_client.get_credit_operation(event.event_id)
            except NewApiCreditError as exc:
                log.warning(f'[CreditOutbox] 对账查询失败 event_id={event.event_id}: {exc}')
                continue
            if outcome is not None and outcome.succeeded:
                # NewAPI 其实成功了，只是回执没送达：把云端状态收敛过来，不重复发放。
                await CreditOutboxService._settle(event.event_id, outcome)
                replayed += 1
        return {'checked': checked, 'reconciled': replayed}

    @staticmethod
    async def collect_metrics() -> dict[str, float]:
        """采集 outbox 观测值（供定时任务刷新 Gauge）。"""
        async with async_db_session() as db:
            pending = await db.execute(
                text("""
                    SELECT count(*) AS pending_total,
                           COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(created_time))), 0) AS oldest_age
                    FROM hasn_billing.credit_grant_event
                    WHERE status = ANY(:statuses)
                """),
                {'statuses': list(OPEN_STATUSES)},
            )
            pending_total, oldest_age = pending.one()
            unfulfilled = await db.execute(
                text("""
                    SELECT count(*) FROM hasn_billing.pay_order
                    WHERE status = 1 AND fulfillment_status NOT IN ('succeeded', 'not_required')
                """)
            )
            paid_unfulfilled = unfulfilled.scalar() or 0
        return {
            'credit_outbox_pending_total': float(pending_total or 0),
            'credit_outbox_oldest_age_seconds': float(oldest_age or 0),
            'billing_paid_unfulfilled_total': float(paid_unfulfilled),
        }


credit_outbox_service: CreditOutboxService = CreditOutboxService()
