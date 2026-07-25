"""线索购买支付回调（doc93 §4.2 线索付费）——付款成功 → 增加用户可领取线索额度。

线索是**独立支付商品**，按支付订单结算，**不走 new-api 积分**（doc93 line 165 铁律）。
通过 billing 回调注册机制解耦（billing 不依赖 hasn_growth）：本模块在应用启动时
`register_pay_callback('lead_pack', handle_lead_pack_paid)`（registrar 调用），与
`app/hasn/service/app_purchase_callback.py` 同范式。

幂等：`PayOrderService.handle_pay_notify` 在分发前已对订单 `status==1` 短路 + `FOR UPDATE`
锁，保证回调每订单仅触发一次（与 credit_pack/app_purchase 一致），不会重复发放额度。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def handle_lead_pack_paid(db: AsyncSession, *, order: Any) -> None:
    """线索购买支付成功回调 → 增加可领取线索余额（purchased_balance·永不过期）。

    doc94 C2 起收 ``db`` 参与支付回调的同一事务；参数缺失直接抛错让订单进死信，
    不再「打日志后 return」把订单留在假成功。

    :param order: 已支付的订单对象（extra_data.lead_count 为购买条数）
    """
    from backend.common.exception import errors

    user_id = order.user_id
    lead_count = int((order.extra_data or {}).get('lead_count') or 0)
    if lead_count <= 0:
        raise errors.RequestError(msg=f'线索订单 {order.order_no} 缺少 lead_count，拒绝静默放行')

    log.info(
        f'[LeadPack] 线索购买成功: user_id={user_id}, '
        f'leads={lead_count}, amount={order.pay_amount}分, order_no={order.order_no}'
    )
    balance = await lead_pool_query_service.grant_purchased_leads(db, user_id=user_id, count=lead_count)
    log.info(f'[LeadPack] 线索额度发放完成: user_id={user_id}, +{lead_count} → 余额 {balance}')


async def revoke_lead_pack(db: AsyncSession, *, order: Any) -> None:
    """线索购买退款回收（``handle_lead_pack_paid`` 的逆操作·MK-9 退款编排）：扣回本单发放的线索余额。

    据 ``extra_data.lead_count`` 从余额里回收（``revoke_purchased_leads`` 只回收未消费部分、如实 log 缺口）。
    **收 ``db`` 参与 refund_order 单一事务**。
    """
    user_id = order.user_id
    lead_count = int((order.extra_data or {}).get('lead_count') or 0)
    if lead_count <= 0:
        log.error(f'[LeadPack] 退款回收缺少 lead_count: order_no={order.order_no}')
        return
    revoked = await lead_pool_query_service.revoke_purchased_leads(db, user_id=user_id, count=lead_count)
    log.info(f'[LeadPack] 退款回收线索: user_id={user_id}, 应回收 {lead_count} 实回收 {revoked}, order_no={order.order_no}')


def register_lead_pack_callback() -> None:
    """注册线索购买支付回调 — 在应用启动时调用（registrar）。"""
    from backend.app.billing.core.callback import register_pay_callback
    from backend.app.billing.core.fulfillment import (
        KIND_LEAD_PACK,
        register_fulfillment,
        register_refund_handler,
    )

    register_pay_callback('lead_pack', handle_lead_pack_paid)  # 存量兼容
    register_fulfillment(KIND_LEAD_PACK, handle_lead_pack_paid)  # MK-3：offering.kind=lead_pack 发货轴
    register_refund_handler(KIND_LEAD_PACK, revoke_lead_pack)  # MK-9：退款回收轴（扣回线索余额）
    log.info('[LeadPack] 已注册线索购买支付回调 (lead_pack) + 退款回收处理器')
