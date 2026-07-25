"""支付成功与退款回收的履约处理器（doc94 C2 改造后）。

**改造前**：handler 直接发货——另开一个 session 改云端余额、改订阅、再把余额推回 NewAPI。
支付成功与额度到账被混为一谈，回调里任何一步失败都会留下「页面显示支付成功、额度却没到」的订单，
而旧流水没有强唯一约束，重试又可能重复发积分，于是既不能安全重试也不能如实展示。

**改造后**：handler 只在**调用方事务内**写一条 ``credit_grant_event(status=pending)``，
真正碰 NewAPI 的动作交给 outbox worker。于是：

- 支付状态与履约状态成为两个可观察状态；
- 重复回调命中同一幂等键，只会留下一条命令；
- 进程在任意一步崩溃，命令要么随事务一起回滚、要么留在 outbox 等重投，绝不半途丢失。

退款回收沿用「与退款单状态同事务」的既有口径——事务内做的事同样变成了「写回收命令」，
外部支付渠道调用留在事务外，由 worker 在 NewAPI 幂等回收成功后触发。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.service.credit_grant_event_service import (
    EVENT_WALLET_GRANT,
    EVENT_WALLET_REVOKE,
    IdempotencyKeys,
    credit_grant_event_service,
)
from backend.app.billing.service.subscription_contract_service import subscription_contract_service
from backend.common.exception import errors
from backend.common.log import log


def _app_code(order: Any) -> str:
    return (getattr(order, 'extra_data', None) or {}).get('app_code', 'huanxing')


async def _resolve_newapi_user_id(db: AsyncSession, user_id: int, app_code: str) -> int:
    """解析履约目标的 NewAPI 用户 ID。

    解析不到直接抛错让订单进 dead：把命令投到一个不存在的账户上，
    等同于把钱收了却把额度发给空气。
    """
    from backend.app.newapi.crud import llm_newapi_user_mapping_dao

    mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
    if not mapping or not mapping.newapi_user_id:
        raise errors.RequestError(msg=f'用户 {user_id} 尚无 NewAPI 账户映射，无法履约')
    return int(mapping.newapi_user_id)


def _order_credit_amount(order: Any) -> Decimal:
    """取订单快照里的积分数量。

    **售价与积分数量是两个独立商品字段**：绝不用支付金额或汇率反推积分，
    否则调价、优惠券、汇率波动都会悄悄改变用户实际拿到的额度。
    """
    extra = getattr(order, 'extra_data', None) or {}
    raw = extra.get('credit_amount')
    if raw is None:
        raise errors.RequestError(msg=f'订单 {getattr(order, "order_no", "?")} 缺少积分数量快照，拒绝履约')
    amount = Decimal(str(raw))
    if amount <= 0:
        raise errors.RequestError(msg=f'订单 {getattr(order, "order_no", "?")} 的积分数量非法: {raw}')
    return amount


async def handle_credit_pack_paid(db: AsyncSession, *, order: Any) -> None:
    """积分包支付成功：在事务内登记一条永久钱包发放命令。"""
    app_code = _app_code(order)
    newapi_user_id = await _resolve_newapi_user_id(db, order.user_id, app_code)
    credits = _order_credit_amount(order)

    event = await credit_grant_event_service.enqueue(
        db,
        event_type=EVENT_WALLET_GRANT,
        idempotency_key=IdempotencyKeys.payment_wallet(order.order_no),
        user_id=order.user_id,
        newapi_user_id=newapi_user_id,
        app_code=app_code,
        credit_amount=credits,
        order_no=order.order_no,
        payload_extra={'reason': 'credit_pack'},
    )
    order.fulfillment_status = 'pending'
    order.fulfillment_event_id = event.event_id
    log.info(f'[PayCallback] 积分包履约命令已登记: order_no={order.order_no} credits={credits}')


async def handle_subscribe_paid(db: AsyncSession, *, order: Any) -> None:
    """订阅支付成功：建合同 + 登记订阅生效命令（都在同一事务内）。"""
    await subscription_contract_service.activate_from_order(db, order=order)


async def revoke_credit_pack(db: AsyncSession, *, order: Any, refund_no: str | None = None) -> None:
    """积分包退款回收：在退款单事务内登记钱包回收命令。

    钱包余额不足以回收原始发放量时，NewAPI 会返回 ``wallet_credit_insufficient`` 并落终局失败，
    退款转人工审核——余额绝不为负。这一步在这里只写命令，判定发生在 NewAPI。
    """
    app_code = _app_code(order)
    newapi_user_id = await _resolve_newapi_user_id(db, order.user_id, app_code)
    credits = _order_credit_amount(order)
    if not refund_no:
        raise errors.RequestError(msg=f'订单 {order.order_no} 的退款回收缺少退款单号')

    await credit_grant_event_service.enqueue(
        db,
        event_type=EVENT_WALLET_REVOKE,
        idempotency_key=IdempotencyKeys.refund_wallet_revoke(refund_no),
        user_id=order.user_id,
        newapi_user_id=newapi_user_id,
        app_code=app_code,
        credit_amount=credits,
        order_no=order.order_no,
        refund_no=refund_no,
        payload_extra={'reason': 'refund_credit_pack'},
    )
    log.info(f'[PayCallback] 积分包回收命令已登记: refund_no={refund_no} credits={credits}')


async def revoke_subscribe(db: AsyncSession, *, order: Any, refund_no: str | None = None) -> None:
    """订阅退款回收：在退款单事务内登记订阅到期命令。"""
    if not refund_no:
        raise errors.RequestError(msg=f'订单 {order.order_no} 的退款回收缺少退款单号')
    await subscription_contract_service.expire_for_refund(db, order=order, refund_no=refund_no)


def register_callbacks() -> None:
    """注册履约与退款回收处理器 — 在应用启动时调用。"""
    from backend.app.billing.core.fulfillment import (
        KIND_CREDIT_PACK,
        KIND_LLM_TIER,
        register_fulfillment,
        register_refund_handler,
    )

    # 履约轴：按商品目录 offering.kind 分发。旧 order_type 回落分支已删除——
    # P0 之后所有履约必须有 offering_ref，命中不到 kind 直接抛错进 dead letter。
    register_fulfillment(KIND_LLM_TIER, handle_subscribe_paid)
    register_fulfillment(KIND_CREDIT_PACK, handle_credit_pack_paid)
    # 退款回收轴：与履约对称，同样只写命令。
    register_refund_handler(KIND_LLM_TIER, revoke_subscribe)
    register_refund_handler(KIND_CREDIT_PACK, revoke_credit_pack)
    log.info('[PayCallback] 已注册履约处理器 (llm_tier, credit_pack) + 退款回收处理器')
