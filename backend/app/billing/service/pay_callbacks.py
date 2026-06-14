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

from decimal import Decimal
from datetime import timedelta
from typing import Any

from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.service.credit_service import credit_service
from backend.app.billing.service.subscription_service import subscription_service
from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
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

    log.info(f'[PayCallback] 积分包发放完成: user_id={user_id}, credits={credit_amount}')


def register_callbacks() -> None:
    """注册支付成功回调 — 在应用启动时调用"""
    from backend.app.billing.core.callback import register_pay_callback

    register_pay_callback('subscribe', handle_subscribe_paid)
    register_pay_callback('credit_pack', handle_credit_pack_paid)
    log.info('[PayCallback] 已注册订阅支付回调 (subscribe, credit_pack)')
