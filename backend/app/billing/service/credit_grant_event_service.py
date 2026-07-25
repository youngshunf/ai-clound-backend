"""履约事件服务（doc94 C2）：事务内写命令，事务外碰外部系统。

这条纪律是整个重构的要害，也是「saga 与同事务不冲突」的具体实现：

- **事务内**只写 ``credit_grant_event(status=pending)``——写命令是纯数据库操作，
  本来就该和订单/退款单状态翻转原子提交；
- **事务外**才由 outbox worker 调用 NewAPI。支付回调的数据库事务里绝不发 HTTP，
  否则一次网络抖动会把已经收到的钱连同订单状态一起回滚掉。

幂等键取自 doc94 §2.1 的固定全集，由本模块的构造函数生成，**不得在调用点现场拼字符串**——
拼错一个字段就等于关掉幂等保护。
"""

from __future__ import annotations

import uuid

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.common.log import log
from backend.utils.timezone import timezone

# 事件类型（与 NewAPI 内部契约的 operation_type 一一对应）
EVENT_WALLET_GRANT = 'wallet_grant'
EVENT_WALLET_REVOKE = 'wallet_revoke'
EVENT_SUBSCRIPTION_ACTIVATE = 'subscription_activate'
EVENT_SUBSCRIPTION_EXPIRE = 'subscription_expire'

# 事件状态
STATUS_PENDING = 'pending'
STATUS_PROCESSING = 'processing'
STATUS_SUCCEEDED = 'succeeded'
STATUS_RETRYING = 'retrying'
STATUS_DEAD = 'dead'
STATUS_CANCELLED = 'cancelled'

#: 未完成状态集合，供 outbox 扫描与「已支付未履约」监控共用。
OPEN_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_RETRYING)

#: 30 天固定周期（秒）。所有周期都用它计算，绝不使用自然月。
CYCLE_SECONDS = 30 * 24 * 60 * 60


class IdempotencyKeys:
    """doc94 §2.1 的幂等键全集。只能从这里取，不得现场自创。"""

    @staticmethod
    def payment_wallet(order_no: str) -> str:
        return f'payment:{order_no}:wallet'

    @staticmethod
    def subscription_activate(contract_no: str) -> str:
        return f'subscription:{contract_no}:activate'

    @staticmethod
    def subscription_expire(contract_no: str) -> str:
        return f'subscription:{contract_no}:expire'

    @staticmethod
    def refund_wallet_revoke(refund_no: str) -> str:
        return f'refund:{refund_no}:wallet-revoke'

    @staticmethod
    def refund_subscription_expire(refund_no: str) -> str:
        return f'refund:{refund_no}:subscription-expire'

    @staticmethod
    def compensation_wallet_restore(refund_no: str) -> str:
        return f'compensation:{refund_no}:wallet-restore'

    @staticmethod
    def free_activate(user_id: int, policy_version: int, epoch: int) -> str:
        """免费档必须带 policy_version 与 epoch。

        若键只是 ``free:{user_id}:activate``，免费政策撤销后再授予会被自己写下的幂等键
        永久挡住——该用户此生再也发不出第二次免费额度。
        """
        return f'free:{user_id}:{policy_version}:{epoch}:activate'

    @staticmethod
    def admin_grant(grant_no: str) -> str:
        """admin 赠送以**单据号**为键。用 user_id 会把同一用户的第二笔赠送当重放吞掉。"""
        return f'admin:{grant_no}:wallet-grant'

    @staticmethod
    def admin_revoke(revoke_no: str) -> str:
        return f'admin:{revoke_no}:wallet-revoke'

    @staticmethod
    def campaign_bonus(campaign_key: str, campaign_version: int, user_id: int) -> str:
        """活动奖励带 campaign_version：同一活动改额度后可重新发放，同版本对同一用户只发一次。"""
        return f'bonus:{campaign_key}:{campaign_version}:{user_id}'


def format_credits(amount: Decimal | int | float | str) -> str:
    """把积分金额格式化成契约要求的十进制字符串（最多 5 位小数）。

    5 位是硬约束：NewAPI 的 QuotaPerUnit=500000 决定 6 位小数不可整除，
    多带一位会被服务端以 ``invalid_credit_amount`` 拒绝（服务端不做静默四舍五入）。
    """
    value = Decimal(str(amount)).quantize(Decimal('0.00001'))
    text = format(value.normalize(), 'f')
    return text


class CreditGrantEventService:
    """履约事件的写入与状态推进。"""

    @staticmethod
    async def get_by_idempotency_key(db: AsyncSession, idempotency_key: str) -> CreditGrantEvent | None:
        result = await db.execute(select(CreditGrantEvent).where(CreditGrantEvent.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        *,
        event_type: str,
        idempotency_key: str,
        user_id: int,
        newapi_user_id: int,
        app_code: str = 'huanxing',
        credit_amount: Decimal | None = None,
        order_no: str | None = None,
        refund_no: str | None = None,
        subscription_id: int | None = None,
        contract_no: str | None = None,
        payload_extra: dict[str, Any] | None = None,
    ) -> CreditGrantEvent:
        """在**调用方事务内**登记一条待投递的履约命令。

        同幂等键重复登记直接返回既有事件（不新建、不改写），因此重复支付通知、
        重试的回调、并发的补偿动作都只会留下一条命令。

        :raises ValueError: 事件类型非法，或订阅生效缺少必要的周期参数。
        """
        existing = await CreditGrantEventService.get_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            log.info(f'[CreditEvent] 幂等命中，复用既有事件: key={idempotency_key} event_id={existing.event_id}')
            return existing

        payload: dict[str, Any] = {
            'operation_type': event_type,
            'newapi_user_id': newapi_user_id,
        }
        if credit_amount is not None:
            payload['credit_amount'] = format_credits(credit_amount)
        if payload_extra:
            payload.update(payload_extra)

        if event_type == EVENT_SUBSCRIPTION_ACTIVATE:
            missing = {'external_subscription_id', 'start_at', 'cycle_seconds'} - set(payload)
            if missing:
                raise ValueError(f'subscription_activate 缺少必填字段: {sorted(missing)}')
            if payload['cycle_seconds'] != CYCLE_SECONDS:
                raise ValueError(f'cycle_seconds 必须为 {CYCLE_SECONDS}（30 天固定周期）')
        elif event_type == EVENT_SUBSCRIPTION_EXPIRE:
            if 'external_subscription_id' not in payload:
                raise ValueError('subscription_expire 缺少 external_subscription_id')
            payload.pop('credit_amount', None)
        elif event_type not in (EVENT_WALLET_GRANT, EVENT_WALLET_REVOKE):
            raise ValueError(f'未知履约事件类型: {event_type}')

        event = CreditGrantEvent(
            event_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            event_type=event_type,
            app_code=app_code,
            user_id=user_id,
            newapi_user_id=newapi_user_id,
            order_no=order_no,
            refund_no=refund_no,
            subscription_id=subscription_id,
            contract_no=contract_no,
            credit_amount=Decimal(str(credit_amount)) if credit_amount is not None else None,
            payload=payload,
            status=STATUS_PENDING,
            next_attempt_at=timezone.now(),
        )
        db.add(event)
        await db.flush()
        log.info(
            f'[CreditEvent] 登记履约命令: type={event_type} key={idempotency_key} '
            f'event_id={event.event_id} user_id={user_id}'
        )
        return event


credit_grant_event_service: CreditGrantEventService = CreditGrantEventService()
