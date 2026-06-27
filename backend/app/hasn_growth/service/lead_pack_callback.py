"""线索购买支付回调（doc93 §4.2 线索付费）——付款成功 → 增加用户可领取线索额度。

线索是**独立支付商品**，按支付订单结算，**不走 new-api 积分**（doc93 line 165 铁律）。
通过 billing 回调注册机制解耦（billing 不依赖 hasn_growth）：本模块在应用启动时
`register_pay_callback('lead_pack', handle_lead_pack_paid)`（registrar 调用），与
`app/hasn/service/app_purchase_callback.py` 同范式。

幂等：`PayOrderService.handle_pay_notify` 在分发前已对订单 `status==1` 短路 + `FOR UPDATE`
锁，保证回调每订单仅触发一次（与 credit_pack/app_purchase 一致），不会重复发放额度。
"""

from typing import Any

from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.common.log import log
from backend.database.db import async_db_session


async def handle_lead_pack_paid(order: Any) -> None:
    """线索购买支付成功回调 → 增加可领取线索余额（purchased_balance·永不过期）。

    :param order: 已支付的订单对象（extra_data.lead_count 为购买条数）
    """
    user_id = order.user_id
    lead_count = int((order.extra_data or {}).get('lead_count') or 0)
    if lead_count <= 0:
        log.error(f'[LeadPack] 线索订单缺少 lead_count: order_no={order.order_no}')
        return

    log.info(
        f'[LeadPack] 线索购买成功: user_id={user_id}, '
        f'leads={lead_count}, amount={order.pay_amount}分, order_no={order.order_no}'
    )
    async with async_db_session.begin() as db:
        balance = await lead_pool_query_service.grant_purchased_leads(db, user_id=user_id, count=lead_count)
    log.info(f'[LeadPack] 线索额度发放完成: user_id={user_id}, +{lead_count} → 余额 {balance}')


def register_lead_pack_callback() -> None:
    """注册线索购买支付回调 — 在应用启动时调用（registrar）。"""
    from backend.app.billing.core.callback import register_pay_callback

    register_pay_callback('lead_pack', handle_lead_pack_paid)
    log.info('[LeadPack] 已注册线索购买支付回调 (lead_pack)')
