"""支付成功业务回调 — 订阅支付完成后的积分发放 + new-api 额度同步

支付模块通过 dispatch_pay_success('subscribe', order) 触发此回调。
本模块在 user_tier 模块启动时通过 register_callbacks() 注册。

处理流程：
1. 升级 user_subscription 记录（等级、周期、状态）
2. 根据套餐配置发放积分到 user_credit_balance
3. 续期/激活用户的 API Key
4. 同步 new-api 额度

@author Ysf
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.service.credit_service import credit_service
from backend.app.billing.service.subscription_service import subscription_service
from backend.app.newapi.credit_sync_service import credit_sync_service
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone


async def handle_subscribe_paid(order: PayOrder) -> None:
    """订阅支付成功回调

    由支付模块在 handle_pay_notify 事务中调用。
    因为 handle_pay_notify 已在事务中，这里需要获取独立的 db session。

    :param order: 已支付的订单对象
    """
    user_id = order.user_id
    target_tier = order.target_tier
    billing_cycle = order.billing_cycle or 'monthly'
    app_code = (order.extra_data or {}).get('app_code', 'huanxing')

    log.info(
        f'[PayCallback] 订阅支付成功: user_id={user_id}, '
        f'tier={target_tier}, cycle={billing_cycle}, '
        f'amount={order.pay_amount}分, order_no={order.order_no}'
    )

    async with async_db_session.begin() as db:
        # 1. 获取目标套餐配置
        tier_config = await subscription_tier_dao.select_model_by_column(
            db, tier_name=target_tier, app_code=app_code, enabled=True
        )
        if not tier_config:
            log.error(f'[PayCallback] 套餐配置不存在: tier={target_tier}, app={app_code}')
            return

        # 2. 升级用户订阅 + 续期 API Key + 同步 new-api quota
        await subscription_service.upgrade_subscription(
            db,
            user_id=user_id,
            new_tier=target_tier,
            subscription_type=billing_cycle,
            app_code=app_code,
        )

        # 3. 发放积分到用户账户
        monthly_credits = tier_config.monthly_credits
        if billing_cycle == 'yearly':
            # 年度订阅：首次发放一个月的积分，后续由定时任务按月发放
            grant_credits = monthly_credits
            expires_at = timezone.now() + timedelta(days=30)
            description = f'{tier_config.display_name}年度订阅首月赠送积分'
        else:
            # 月度订阅：发放当月积分
            grant_credits = monthly_credits
            expires_at = timezone.now() + timedelta(days=30)
            description = f'{tier_config.display_name}月度订阅赠送积分'

        await credit_service.add_credits(
            db,
            user_id=user_id,
            credits=grant_credits,
            transaction_type='subscription_grant',
            reference_id=order.order_no,
            reference_type='pay_order',
            description=description,
            is_purchased=False,
            expires_at=expires_at,
            app_code=app_code,
        )

        # 4. 入账后令 new-api 可用额度 = 账本剩余×RATE（§5A.4/D6；权威镜像，
        #    覆盖 upgrade_subscription 的 tier-quota 推送，确保含本次赠送的全部余额）
        await credit_sync_service.sync_quota_to_balance(db, user_id, app_code=app_code)

        log.info(
            f'[PayCallback] 积分发放完成: user_id={user_id}, '
            f'credits={grant_credits}, tier={target_tier}'
        )


async def handle_credit_pack_paid(order: PayOrder) -> None:
    """积分包购买成功回调

    :param order: 已支付的订单对象
    """
    user_id = order.user_id
    app_code = (order.extra_data or {}).get('app_code', 'huanxing')
    credit_amount = (order.extra_data or {}).get('credit_amount')

    if not credit_amount:
        log.error(f'[PayCallback] 积分包订单缺少 credit_amount: order_no={order.order_no}')
        return

    log.info(
        f'[PayCallback] 积分包购买成功: user_id={user_id}, '
        f'credits={credit_amount}, amount={order.pay_amount}分'
    )

    async with async_db_session.begin() as db:
        await credit_service.add_credits(
            db,
            user_id=user_id,
            credits=Decimal(str(credit_amount)),
            transaction_type='purchase',
            reference_id=order.order_no,
            reference_type='pay_order',
            description=f'购买积分包 ({credit_amount} 积分)',
            is_purchased=True,  # 购买的积分永不过期
            expires_at=None,
            app_code=app_code,
        )

        # 入账后把 new-api 可用额度推到账本剩余×RATE（§5A.4/D6，补缺口 2：
        # 此前积分包只写账本不推 new-api → 买了用不了）
        await credit_sync_service.sync_quota_to_balance(db, user_id, app_code=app_code)

    log.info(f'[PayCallback] 积分包发放完成: user_id={user_id}, credits={credit_amount}')


async def revoke_subscribe(db: AsyncSession, *, order: Any) -> None:
    """订阅退款回收（``handle_subscribe_paid`` 的逆操作·MK-9 退款编排）：扣回赠送积分 + 降级免费档。

    收 ``db`` 参与 refund_order 单一事务：
      ① 按订单 ``target_tier`` 查套餐月度积分，从账本**扣回**本单赠送积分（``deduct_credits``——
         余额不足会抛 ``InsufficientCreditsError``，refund_order 事务回滚、拒绝退款：不允许「退钱但积分已花光无法收回」）；
      ② 订阅降级到免费档（``downgrade_to_free``）；③ 重推 new-api 可用额度对齐账本。

    **保守语义**：单订单模型下退款即回落免费档（不追溯上一档订阅）；真机退款为福仔专项、场景稀少，
    该保守逆操作足够诚实、可审计。
    """
    user_id = order.user_id
    target_tier = order.target_tier
    app_code = (order.extra_data or {}).get('app_code', 'huanxing')

    tier_config = await subscription_tier_dao.select_model_by_column(
        db, tier_name=target_tier, app_code=app_code, enabled=True
    )
    grant_credits = tier_config.monthly_credits if tier_config else Decimal(0)
    if grant_credits and grant_credits > 0:
        await credit_service.deduct_credits(
            db,
            user_id=user_id,
            credits=Decimal(str(grant_credits)),
            reference_id=order.order_no,
            reference_type='refund',
            description=f'退款回收订阅赠送积分（{target_tier}）',
            app_code=app_code,
        )
    await subscription_service.downgrade_to_free(db, user_id, app_code=app_code)
    await credit_sync_service.sync_quota_to_balance(db, user_id, app_code=app_code)
    log.info(f'[PayCallback] 订阅退款回收完成: user_id={user_id}, 扣回积分={grant_credits}, 降级免费档')


async def revoke_credit_pack(db: AsyncSession, *, order: Any) -> None:
    """积分包退款回收（``handle_credit_pack_paid`` 的逆操作·MK-9 退款编排）：从账本扣回本单发放积分。

    收 ``db`` 参与 refund_order 单一事务：扣回 ``extra_data.credit_amount``（``deduct_credits`` 余额不足
    抛 ``InsufficientCreditsError`` → 事务回滚、拒绝退款：不允许「退钱但积分已消费无法收回」），再重推 new-api。
    """
    user_id = order.user_id
    app_code = (order.extra_data or {}).get('app_code', 'huanxing')
    credit_amount = (order.extra_data or {}).get('credit_amount')
    if not credit_amount:
        log.error(f'[PayCallback] 积分包退款回收缺少 credit_amount: order_no={order.order_no}')
        return

    await credit_service.deduct_credits(
        db,
        user_id=user_id,
        credits=Decimal(str(credit_amount)),
        reference_id=order.order_no,
        reference_type='refund',
        description=f'退款回收积分包 ({credit_amount} 积分)',
        app_code=app_code,
    )
    await credit_sync_service.sync_quota_to_balance(db, user_id, app_code=app_code)
    log.info(f'[PayCallback] 积分包退款回收完成: user_id={user_id}, 扣回积分={credit_amount}')


def register_callbacks() -> None:
    """注册支付成功回调 — 在应用启动时调用"""
    from backend.app.billing.core.callback import register_pay_callback
    from backend.app.billing.core.fulfillment import (
        KIND_CREDIT_PACK,
        KIND_LLM_TIER,
        register_fulfillment,
        register_refund_handler,
    )

    # 旧 order_type 分发（存量兼容，无 offering_ref 的订单回落这里）
    register_pay_callback('subscribe', handle_subscribe_paid)
    register_pay_callback('credit_pack', handle_credit_pack_paid)
    # MK-3：内核发货轴——按商品目录 offering.kind 分发（新订单走这里）
    register_fulfillment(KIND_LLM_TIER, handle_subscribe_paid)
    register_fulfillment(KIND_CREDIT_PACK, handle_credit_pack_paid)
    # MK-9：退款回收轴——按 offering.kind 反向回收（扣回积分/降级）
    register_refund_handler(KIND_LLM_TIER, revoke_subscribe)
    register_refund_handler(KIND_CREDIT_PACK, revoke_credit_pack)
    log.info('[PayCallback] 已注册订阅支付回调 (subscribe/llm_tier, credit_pack) + 退款回收处理器')
