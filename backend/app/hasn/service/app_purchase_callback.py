"""AI-Native 应用购买支付成功回调（C5，设计 §5.4）。

支付模块经 ``dispatch_pay_success('app_purchase', order)`` 触发：购买成功 → 写
``hasn_app_entitlement``（source=purchase, order_ref=订单号, expires_at 按 catalog.billing_cycle）。
与订阅回调（user_tier/pay_callbacks.py）同构，但权益落在 hasn 应用目录域。

订单约定：``order_type='app_purchase'``，``extra_data={'app_id': <catalog.app_id>}``。
权益主体取 owner（``order.user_id`` → HasnHumans.hasn_id）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.service import app_catalog_service
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement


async def apply_app_purchase(db: AsyncSession, *, order: Any) -> HasnAppEntitlement | None:
    """购买成功核心：解析 owner + 算到期 + 写权益。返回写入的权益行（缺 app_id / owner 时返回 None）。

    抽出 session 参数版便于真实联调测试（rollback 隔离）；生产入口 ``handle_app_purchase_paid`` 自取独立 session。
    """
    app_id = (order.extra_data or {}).get('app_id')
    if not app_id:
        log.error(f'[AppPurchase] 订单缺少 app_id: order_no={order.order_no}')
        return None

    owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=order.user_id)
    if not owner_hasn_id:
        log.error(f'[AppPurchase] user_id={order.user_id} 无 owner 映射，跳过授予: order_no={order.order_no}')
        return None

    cat = await app_catalog_service.get_catalog(db, app_id=app_id)
    # 到期：优先订单 billing_cycle，回落 catalog.billing_cycle（once=永久买断）。
    billing_cycle = order.billing_cycle or (cat.billing_cycle if cat else None)
    expires_at = app_catalog_service.purchase_expiry(billing_cycle)
    ent = await app_catalog_service.grant_entitlement(
        db,
        app_id=app_id,
        subject_type='owner',
        subject_id=owner_hasn_id,
        source='purchase',
        order_ref=order.order_no,
        expires_at=expires_at,
    )
    log.info(
        f'[AppPurchase] 权益已授予: app_id={app_id}, owner={owner_hasn_id}, '
        f'entitlement_id={ent.id}, expires_at={ent.expires_at}'
    )
    return ent


async def handle_app_purchase_paid(order: Any) -> None:
    """应用购买支付成功回调 → 写 owner 维度 active 权益（幂等：已有 active 不重复发）。

    与订阅回调（user_tier/pay_callbacks.py）一致，取独立 session 提交。
    """
    log.info(
        f'[AppPurchase] 应用购买支付成功: user_id={order.user_id}, '
        f'app_id={(order.extra_data or {}).get("app_id")}, '
        f'amount={order.pay_amount}分, order_no={order.order_no}'
    )
    async with async_db_session.begin() as db:
        await apply_app_purchase(db, order=order)


def register_app_purchase_callback() -> None:
    """注册应用购买支付回调 — 在应用启动时调用（registrar）。"""
    from backend.app.pay.core.callback import register_pay_callback

    register_pay_callback('app_purchase', handle_app_purchase_paid)
    log.info('[AppPurchase] 已注册应用购买支付回调 (app_purchase)')
