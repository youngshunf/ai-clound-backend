"""定时任务：年度订阅积分发放 + API Key 过期检查
@author Ysf
"""

from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from sqlalchemy import select, and_, update

from backend.app.llm.enums import ApiKeyStatus
from backend.app.llm.model.user_api_key import UserApiKey
from backend.app.billing.model import UserSubscription, UserCreditBalance, CreditTransaction
from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone


@shared_task(name='grant_yearly_subscription_credits')
async def grant_yearly_subscription_credits() -> str:
    """
    年度订阅用户每月积分发放任务
    
    每天凌晨执行，检查符合条件的年度订阅用户：
    - subscription_type = 'yearly'
    - status = 'active'
    - next_grant_date <= now
    - subscription_end_date > now (订阅未过期)
    
    为符合条件的用户：
    1. 创建积分余额记录
    2. 更新 next_grant_date 为下个月
    3. 记录交易
    """
    now = timezone.now()
    granted_count = 0
    error_count = 0
    
    async with async_db_session.begin() as db:
        # 查询需要发放积分的年度订阅用户
        stmt = select(UserSubscription).where(
            and_(
                UserSubscription.subscription_type == 'yearly',
                UserSubscription.status == 'active',
                UserSubscription.next_grant_date <= now,
                UserSubscription.subscription_end_date > now,
            )
        )
        result = await db.execute(stmt)
        subscriptions = result.scalars().all()
        
        log.info(f'[YearlyGrant] 找到 {len(subscriptions)} 个需要发放积分的年度订阅用户')
        
        for subscription in subscriptions:
            try:
                # 获取等级配置
                tier = await subscription_tier_dao.select_model_by_column(
                    db, tier_name=subscription.tier
                )
                if not tier:
                    log.warning(f'[YearlyGrant] 用户 {subscription.user_id} 的订阅等级 {subscription.tier} 不存在')
                    error_count += 1
                    continue
                
                monthly_credits = tier.monthly_credits
                
                # 计算下次发放时间和积分有效期
                next_grant = subscription.next_grant_date + timedelta(days=30)
                cycle_end = subscription.next_grant_date + timedelta(days=30)
                
                # 确保不超过订阅结束时间
                if next_grant > subscription.subscription_end_date:
                    next_grant = None  # 最后一次发放，不再设置下次发放时间
                
                # 计算发放的月份数（从订阅开始算起）
                if subscription.subscription_start_date:
                    months_elapsed = (subscription.next_grant_date - subscription.subscription_start_date).days // 30 + 1
                else:
                    months_elapsed = 1
                
                # 创建积分余额记录
                balance = UserCreditBalance(
                    user_id=subscription.user_id,
                    credit_type='monthly',
                    original_amount=monthly_credits,
                    used_amount=Decimal('0'),
                    remaining_amount=monthly_credits,
                    expires_at=cycle_end,
                    granted_at=now,
                    source_type='yearly_subscription_grant',
                    description=f'年度订阅: {subscription.tier} (第{months_elapsed}个月)',
                )
                db.add(balance)
                
                # 获取当前总积分（用于记录交易）
                from sqlalchemy import func
                from backend.app.billing.model import UserCreditBalance as UCB
                balance_stmt = select(func.coalesce(func.sum(UCB.remaining_amount), 0)).where(
                    and_(
                        UCB.user_id == subscription.user_id,
                        UCB.remaining_amount > 0,
                    )
                )
                balance_result = await db.execute(balance_stmt)
                current_balance = Decimal(str(balance_result.scalar() or 0))
                
                # 记录交易
                transaction = CreditTransaction(
                    user_id=subscription.user_id,
                    transaction_type='yearly_grant',
                    credits=monthly_credits,
                    balance_before=current_balance,
                    balance_after=current_balance + monthly_credits,
                    description=f'年度订阅月度赠送: {subscription.tier} (第{months_elapsed}个月)',
                    extra_data={
                        'tier': subscription.tier,
                        'month': months_elapsed,
                        'subscription_type': 'yearly',
                    },
                )
                db.add(transaction)
                
                # 更新订阅的下次发放时间和计费周期
                subscription.next_grant_date = next_grant
                subscription.billing_cycle_start = now
                subscription.billing_cycle_end = cycle_end
                subscription.current_credits = current_balance + monthly_credits
                
                granted_count += 1
                log.info(f'[YearlyGrant] 用户 {subscription.user_id} 发放 {monthly_credits} 积分成功 (第{months_elapsed}个月)')
                
            except Exception as e:
                log.error(f'[YearlyGrant] 用户 {subscription.user_id} 发放积分失败: {e}')
                error_count += 1
                continue
    
    result_msg = f'年度订阅积分发放完成: 成功 {granted_count} 个, 失败 {error_count} 个'
    log.info(f'[YearlyGrant] {result_msg}')
    return result_msg


@shared_task(name='check_expired_api_keys')
async def check_expired_api_keys() -> str:
    """
    每日检查并标记过期的 API Key

    查找所有状态为 ACTIVE 但 expires_at < now 的 Key，
    将其状态更新为 EXPIRED。
    """
    now = timezone.now()

    async with async_db_session.begin() as db:
        # 批量更新过期的 API Key
        stmt = (
            update(UserApiKey)
            .where(
                and_(
                    UserApiKey.status == ApiKeyStatus.ACTIVE,
                    UserApiKey.expires_at.isnot(None),
                    UserApiKey.expires_at < now,
                )
            )
            .values(status=ApiKeyStatus.EXPIRED)
        )
        result = await db.execute(stmt)
        expired_count = result.rowcount

    result_msg = f'API Key 过期检查完成: {expired_count} 个 Key 已标记为过期'
    log.info(f'[ExpiredKeyCheck] {result_msg}')
    return result_msg


@shared_task(name='expire_overdue_subscriptions')
async def expire_overdue_subscriptions() -> str:
    """每日检查并标记过期订阅（收敛存量 status）。

    付费订阅（subscription_end_date 非空且 < now）若仍为 active → 置 expired。
    免费版 subscription_end_date 为 NULL，不参与（永不过期）。
    与 /info 读取时的状态重算（credit_service.get_user_credits_info）口径一致——读路径已
    实时纠正显示，本任务保证 DB 存量 status 也收敛，供 admin 列表等其它读路径正确。
    """
    now = timezone.now()

    async with async_db_session.begin() as db:
        stmt = (
            update(UserSubscription)
            .where(
                and_(
                    UserSubscription.status == 'active',
                    UserSubscription.tier != 'free',
                    UserSubscription.subscription_end_date.isnot(None),
                    UserSubscription.subscription_end_date < now,
                )
            )
            .values(status='expired')
        )
        result = await db.execute(stmt)
        expired_count = result.rowcount

    result_msg = f'订阅过期检查完成: {expired_count} 个订阅已标记为过期'
    log.info(f'[ExpiredSubscription] {result_msg}')
    return result_msg
