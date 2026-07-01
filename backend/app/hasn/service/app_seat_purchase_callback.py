"""企业席位购买支付回调（doc04 §6.4③）——付款成功 → 企业权益累加席位。

与 ``app_purchase_callback``（owner 维度）/ ``lead_pack_callback`` 同范式，经 billing 回调注册解耦。
订单约定：``order_type='app_seat'``，``extra_data={'app_id','enterprise_id','seats'}``；到期按 billing_cycle。

**S2 幂等**：``PayOrderService.handle_pay_notify`` 在分发前已对订单 ``status==1`` 短路 + ``FOR UPDATE`` 锁，
保证回调每订单仅触发一次（同 credit_pack/app_purchase/lead_pack），故 ``seats_total`` 累加不会因重放而重复。
席位结算本身用两步（grant 保证权益行存在 + FOR UPDATE 累加），规避 grant_entitlement 幂等吞席位。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.service import app_seat_service
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement


async def settle_app_seat_purchase(db: AsyncSession, *, order: Any) -> HasnAppEntitlement | None:
    """席位购买结算核心：解析企业/席位数 → 累加 seats_total。返回权益行（缺字段返回 None）。

    抽 session 参数版便于真实联调测试（rollback 隔离）；生产入口 ``handle_app_seat_paid`` 自取独立 session。
    """
    extra = order.extra_data or {}
    app_id = extra.get('app_id')
    enterprise_id = extra.get('enterprise_id')
    seats = int(extra.get('seats') or 0)
    if not app_id or enterprise_id is None or seats <= 0:
        log.error(
            f'[AppSeat] 订单字段缺失: order_no={order.order_no}, '
            f'app_id={app_id}, enterprise_id={enterprise_id}, seats={seats}'
        )
        return None

    ent = await app_seat_service.settle_seat_purchase(
        db,
        enterprise_id=int(enterprise_id),
        app_id=app_id,
        seats=seats,
        billing_cycle=order.billing_cycle,
        order_ref=order.order_no,
    )
    log.info(
        f'[AppSeat] 席位已结算: app_id={app_id}, enterprise_id={enterprise_id}, '
        f'+{seats} → seats_total={ent.seats_total}, entitlement_id={ent.id}'
    )
    return ent


async def handle_app_seat_paid(order: Any) -> None:
    """企业席位购买支付成功回调 → 累加企业权益 seats_total（独立 session 提交）。"""
    log.info(
        f'[AppSeat] 席位购买支付成功: user_id={order.user_id}, '
        f'app_id={(order.extra_data or {}).get("app_id")}, '
        f'seats={(order.extra_data or {}).get("seats")}, '
        f'amount={order.pay_amount}分, order_no={order.order_no}'
    )
    async with async_db_session.begin() as db:
        await settle_app_seat_purchase(db, order=order)


def register_app_seat_purchase_callback() -> None:
    """注册企业席位购买支付回调 — 在应用启动时调用（registrar）。"""
    from backend.app.billing.core.callback import register_pay_callback

    register_pay_callback('app_seat', handle_app_seat_paid)
    log.info('[AppSeat] 已注册企业席位购买支付回调 (app_seat)')
