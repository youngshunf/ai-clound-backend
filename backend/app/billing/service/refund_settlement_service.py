"""退款 saga 的第二段（doc94 §4.4 F1）：额度回收成功之后才调支付渠道。

顺序不能反。**先回收额度、再退钱**：

- 若先退钱再回收，中间任何失败都会留下「钱退了、额度还在」——用户白拿一笔额度；
- 反过来若回收成功而渠道明确失败，还能用反向补偿事件把额度还回去，用户不吃亏。

渠道**超时或结果未知**时绝不补偿：必须先查单确认渠道到底退没退。
「一边补发额度、一边渠道其实退成功了」等于用户双得，这是本模块最需要防住的错。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from backend.app.billing.crud.crud_pay_order import pay_order_dao
from backend.app.billing.crud.crud_pay_refund import pay_refund_dao
from backend.app.billing.model.pay_refund import PayRefund
from backend.app.billing.service.credit_grant_event_service import (
    EVENT_WALLET_GRANT,
    IdempotencyKeys,
    credit_grant_event_service,
)
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

@dataclass(slots=True)
class _RefundSnapshot:
    """跨会话传递的退款快照：会话关掉后 ORM 实例就不能再读了。"""

    order_no: str
    user_id: int
    channel_code: str
    pay_amount: int
    refund_amount: int
    reason: str


# 退款单状态：0=待处理 1=成功 2=失败
REFUND_STATUS_PENDING = 0
REFUND_STATUS_SUCCEEDED = 1
REFUND_STATUS_FAILED = 2


class RefundSettlementService:
    """额度已回收后的渠道退款结算。"""

    @staticmethod
    async def settle_channel_refund(refund_no: str) -> str:
        """对一张已完成额度回收的退款单执行渠道退款。

        :return: ``succeeded`` / ``failed`` / ``pending``（结果未知，留待下轮重试）
        """
        async with async_db_session() as db:
            refund = (
                await db.execute(select(PayRefund).where(PayRefund.refund_no == refund_no))
            ).scalar_one_or_none()
            if refund is None:
                log.warning(f'[Refund] 退款单不存在，跳过渠道结算: refund_no={refund_no}')
                return 'failed'
            if refund.status != REFUND_STATUS_PENDING:
                # 幂等：已终态的退款单不再重复调渠道。
                return 'succeeded' if refund.status == REFUND_STATUS_SUCCEEDED else 'failed'
            order = await pay_order_dao.get_by_order_no(db, refund.order_no)
            if order is None:
                log.error(f'[Refund] 退款单对应订单不存在: refund_no={refund_no} order_no={refund.order_no}')
                return 'failed'
            snapshot = _RefundSnapshot(
                order_no=str(order.order_no),
                user_id=int(order.user_id),
                channel_code=str(order.channel_code or ''),
                pay_amount=int(order.pay_amount),
                refund_amount=int(refund.refund_amount),
                reason=str(refund.reason or '管理端退款'),
            )

        from backend.app.billing.service.pay_order_service import PayOrderService

        try:
            async with async_db_session() as db:
                channel, merchant_config = await PayOrderService._resolve_channel(db, snapshot.channel_code)
            result = PayOrderService._invoke_channel_refund(
                channel,
                merchant_config,
                order_no=snapshot.order_no,
                refund_no=refund_no,
                refund_amount=snapshot.refund_amount,
                total_amount=snapshot.pay_amount,
                reason=snapshot.reason,
            )
        except Exception as exc:
            # 渠道结果未知：**不补偿**、不置失败，留在 pending 等下一轮重试或人工查单。
            # 这里若贸然补发额度，而渠道其实退款成功了，用户就双得了。
            log.warning(f'[Refund] 渠道退款结果未知，保持待处理等待重试: refund_no={refund_no}: {exc!r}')
            return 'pending'

        channel_refund_no = (result or {}).get('channel_refund_no') or (result or {}).get('refund_id')
        async with async_db_session.begin() as db:
            await pay_refund_dao.update_status(
                db,
                refund_no,
                status=REFUND_STATUS_SUCCEEDED,
                channel_refund_no=channel_refund_no,
                success_time=timezone.now(),
            )
            await pay_order_dao.mark_refunded(db, order_no=snapshot.order_no, refund_amount=snapshot.refund_amount)
        log.info(f'[Refund] 渠道退款完成: refund_no={refund_no} channel_refund_no={channel_refund_no}')
        return 'succeeded'

    @staticmethod
    async def compensate_failed_refund(refund_no: str, *, reason: str = 'channel_refund_failed') -> None:
        """渠道**明确**退款失败时，把已回收的额度补回去。

        只在渠道给出确定失败结论时调用；超时/未知一律先查单，绝不在这里补偿。
        """
        async with async_db_session.begin() as db:
            refund = (
                await db.execute(select(PayRefund).where(PayRefund.refund_no == refund_no).with_for_update())
            ).scalar_one_or_none()
            if refund is None or refund.status == REFUND_STATUS_SUCCEEDED:
                return
            order = await pay_order_dao.get_by_order_no(db, refund.order_no)
            if order is None:
                log.error(f'[Refund] 补偿失败：退款单对应订单不存在 refund_no={refund_no}')
                return
            extra = dict(order.extra_data or {})
            credits = extra.get('credit_amount')
            app_code = extra.get('app_code', 'huanxing')
            refund.status = REFUND_STATUS_FAILED

            if credits is None:
                # 非积分类商品（应用/席位/线索）的回收补偿由各自模块负责，这里只如实记账。
                log.critical(
                    f'[Refund] 渠道退款失败且订单非积分类，需人工对账: refund_no={refund_no} order_no={refund.order_no}'
                )
                return

            from backend.app.billing.service.pay_callbacks import _resolve_newapi_user_id

            newapi_user_id = await _resolve_newapi_user_id(db, order.user_id, app_code)
            await credit_grant_event_service.enqueue(
                db,
                event_type=EVENT_WALLET_GRANT,
                idempotency_key=IdempotencyKeys.compensation_wallet_restore(refund_no),
                user_id=order.user_id,
                newapi_user_id=newapi_user_id,
                app_code=app_code,
                credit_amount=Decimal(str(credits)),
                order_no=order.order_no,
                refund_no=refund_no,
                payload_extra={'reason': reason[:64]},
            )
        log.error(f'[Refund] 渠道退款明确失败，已登记反向补偿事件: refund_no={refund_no}')


refund_settlement_service: RefundSettlementService = RefundSettlementService()
