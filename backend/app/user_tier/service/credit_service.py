"""积分核心服务 - 积分计算、检查和扣除逻辑
@author Ysf
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.user_tier.crud.crud_credit_transaction import credit_transaction_dao
from backend.app.user_tier.crud.crud_model_credit_rate import model_credit_rate_dao
from backend.app.user_tier.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.user_tier.crud.crud_user_subscription import user_subscription_dao
from backend.app.user_tier.model import CreditTransaction, ModelCreditRate, SubscriptionTier, UserSubscription
from backend.app.user_tier.schema.credit_transaction import CreateCreditTransactionParam
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone


class InsufficientCreditsError(errors.HTTPError):
    """积分不足错误"""

    def __init__(self, current_credits: Decimal, required_credits: Decimal) -> None:
        super().__init__(
            code=402,
            msg=f'Insufficient credits: current={current_credits}, required={required_credits}',
        )
        self.current_credits = current_credits
        self.required_credits = required_credits


class SubscriptionNotFoundError(errors.HTTPError):
    """订阅未找到错误"""

    def __init__(self, user_id: int) -> None:
        super().__init__(code=404, msg=f'Subscription not found for user: {user_id}')


class SubscriptionExpiredError(errors.HTTPError):
    """订阅已过期错误"""

    def __init__(self, user_id: int) -> None:
        super().__init__(code=403, msg=f'Subscription expired for user: {user_id}')


class CreditService:
    """积分核心服务"""

    # 默认积分费率 (如果模型没有配置费率)
    DEFAULT_BASE_CREDIT_PER_1K = Decimal('1.0')
    DEFAULT_INPUT_MULTIPLIER = Decimal('1.0')
    DEFAULT_OUTPUT_MULTIPLIER = Decimal('1.0')

    async def get_or_create_subscription(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> UserSubscription:
        """
        获取用户订阅，如果不存在则创建免费订阅

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 用户订阅
        """
        # 查询用户订阅
        subscription = await user_subscription_dao.select_model_by_column(db, user_id=user_id)

        if subscription:
            return subscription

        # 创建免费订阅
        log.info(f'[Credit] Creating free subscription for user {user_id}')
        subscription = await self._create_free_subscription(db, user_id)
        return subscription

    async def _create_free_subscription(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> UserSubscription:
        """创建免费订阅"""
        # 获取免费等级配置
        free_tier = await subscription_tier_dao.select_model_by_column(db, tier_name='free')
        monthly_credits = free_tier.monthly_credits if free_tier else Decimal('100000')

        now = timezone.now()
        cycle_end = now + timedelta(days=30)

        subscription = UserSubscription(
            user_id=user_id,
            tier='free',
            monthly_credits=monthly_credits,
            current_credits=monthly_credits,
            used_credits=Decimal('0'),
            purchased_credits=Decimal('0'),
            billing_cycle_start=now,
            billing_cycle_end=cycle_end,
            status='active',
            auto_renew=True,
        )

        db.add(subscription)
        await db.flush()
        await db.refresh(subscription)

        # 记录月度赠送交易
        await self._record_transaction(
            db,
            user_id=user_id,
            transaction_type='monthly_grant',
            credits=monthly_credits,
            balance_before=Decimal('0'),
            balance_after=monthly_credits,
            description=f'免费版月度赠送积分',
        )

        return subscription

    async def get_model_credit_rate(
        self,
        db: AsyncSession,
        model_id: int,
    ) -> ModelCreditRate | None:
        """
        获取模型积分费率

        :param db: 数据库会话
        :param model_id: 模型 ID
        :return: 模型积分费率
        """
        return await model_credit_rate_dao.select_model_by_column(db, model_id=model_id, enabled=True)

    def calculate_credits(
        self,
        input_tokens: int,
        output_tokens: int,
        rate: ModelCreditRate | None = None,
    ) -> Decimal:
        """
        计算积分消耗

        :param input_tokens: 输入 tokens
        :param output_tokens: 输出 tokens
        :param rate: 模型积分费率
        :return: 积分消耗
        """
        base_credit = rate.base_credit_per_1k_tokens if rate else self.DEFAULT_BASE_CREDIT_PER_1K
        input_mult = rate.input_multiplier if rate else self.DEFAULT_INPUT_MULTIPLIER
        output_mult = rate.output_multiplier if rate else self.DEFAULT_OUTPUT_MULTIPLIER

        input_credits = (Decimal(input_tokens) / 1000) * base_credit * input_mult
        output_credits = (Decimal(output_tokens) / 1000) * base_credit * output_mult

        total_credits = input_credits + output_credits

        # 向上取整到小数点后2位
        return total_credits.quantize(Decimal('0.01'))

    async def check_credits(
        self,
        db: AsyncSession,
        user_id: int,
        estimated_credits: Decimal | None = None,
    ) -> UserSubscription:
        """
        检查用户积分是否足够

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param estimated_credits: 预估需要的积分 (可选)
        :return: 用户订阅
        :raises SubscriptionNotFoundError: 订阅未找到
        :raises SubscriptionExpiredError: 订阅已过期
        :raises InsufficientCreditsError: 积分不足
        """
        subscription = await self.get_or_create_subscription(db, user_id)

        # 检查订阅状态
        if subscription.status != 'active':
            raise SubscriptionExpiredError(user_id)

        # 检查计费周期
        now = timezone.now()
        if now > subscription.billing_cycle_end:
            # 尝试刷新周期
            subscription = await self._refresh_billing_cycle(db, subscription)

        # 检查积分余额
        if estimated_credits and subscription.current_credits < estimated_credits:
            raise InsufficientCreditsError(subscription.current_credits, estimated_credits)

        return subscription

    async def deduct_credits(
        self,
        db: AsyncSession,
        user_id: int,
        credits: Decimal,
        reference_id: str | None = None,
        reference_type: str = 'llm_usage',
        description: str | None = None,
        extra_data: dict | None = None,
    ) -> UserSubscription:
        """
        扣除用户积分 (原子操作)

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param credits: 扣除的积分数量
        :param reference_id: 关联 ID
        :param reference_type: 关联类型
        :param description: 交易描述
        :param extra_data: 扩展数据
        :return: 更新后的订阅
        :raises InsufficientCreditsError: 积分不足
        """
        # 获取并锁定订阅记录 (SELECT FOR UPDATE)
        stmt = (
            select(UserSubscription)
            .where(UserSubscription.user_id == user_id)
            .with_for_update()
        )
        result = await db.execute(stmt)
        subscription = result.scalar_one_or_none()

        if not subscription:
            raise SubscriptionNotFoundError(user_id)

        # 检查余额
        if subscription.current_credits < credits:
            raise InsufficientCreditsError(subscription.current_credits, credits)

        # 记录交易前余额
        balance_before = subscription.current_credits

        # 更新余额
        subscription.current_credits -= credits
        subscription.used_credits += credits

        # 记录交易
        await self._record_transaction(
            db,
            user_id=user_id,
            transaction_type='usage',
            credits=-credits,  # 负数表示消费
            balance_before=balance_before,
            balance_after=subscription.current_credits,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
            extra_data=extra_data,
        )

        log.info(f'[Credit] Deducted {credits} credits from user {user_id}, '
                 f'balance: {balance_before} -> {subscription.current_credits}')

        return subscription

    async def add_credits(
        self,
        db: AsyncSession,
        user_id: int,
        credits: Decimal,
        transaction_type: str = 'purchase',
        reference_id: str | None = None,
        reference_type: str = 'payment',
        description: str | None = None,
        is_purchased: bool = True,
    ) -> UserSubscription:
        """
        增加用户积分

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param credits: 增加的积分数量
        :param transaction_type: 交易类型
        :param reference_id: 关联 ID
        :param reference_type: 关联类型
        :param description: 交易描述
        :param is_purchased: 是否为购买的积分 (购买的积分不会过期)
        :return: 更新后的订阅
        """
        subscription = await self.get_or_create_subscription(db, user_id)

        balance_before = subscription.current_credits

        # 更新余额
        subscription.current_credits += credits
        if is_purchased:
            subscription.purchased_credits += credits

        # 记录交易
        await self._record_transaction(
            db,
            user_id=user_id,
            transaction_type=transaction_type,
            credits=credits,  # 正数表示增加
            balance_before=balance_before,
            balance_after=subscription.current_credits,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
        )

        log.info(f'[Credit] Added {credits} credits to user {user_id}, '
                 f'balance: {balance_before} -> {subscription.current_credits}')

        return subscription

    async def _refresh_billing_cycle(
        self,
        db: AsyncSession,
        subscription: UserSubscription,
    ) -> UserSubscription:
        """
        刷新计费周期

        :param db: 数据库会话
        :param subscription: 用户订阅
        :return: 更新后的订阅
        """
        # 获取等级配置
        tier = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier)
        monthly_credits = tier.monthly_credits if tier else Decimal('100000')

        # 保留购买的积分，重置月度积分
        balance_before = subscription.current_credits
        new_credits = monthly_credits + subscription.purchased_credits

        now = timezone.now()
        subscription.billing_cycle_start = now
        subscription.billing_cycle_end = now + timedelta(days=30)
        subscription.current_credits = new_credits
        subscription.used_credits = Decimal('0')
        subscription.monthly_credits = monthly_credits

        # 如果订阅已过期，重新激活
        if subscription.status == 'expired':
            subscription.status = 'active'

        # 记录月度赠送交易
        await self._record_transaction(
            db,
            user_id=subscription.user_id,
            transaction_type='monthly_grant',
            credits=monthly_credits,
            balance_before=balance_before,
            balance_after=new_credits,
            description=f'{subscription.tier}版月度赠送积分',
        )

        log.info(f'[Credit] Refreshed billing cycle for user {subscription.user_id}, '
                 f'granted {monthly_credits} credits')

        return subscription

    async def _record_transaction(
        self,
        db: AsyncSession,
        user_id: int,
        transaction_type: str,
        credits: Decimal,
        balance_before: Decimal,
        balance_after: Decimal,
        reference_id: str | None = None,
        reference_type: str | None = None,
        description: str | None = None,
        extra_data: dict | None = None,
    ) -> CreditTransaction:
        """记录积分交易"""
        transaction = CreditTransaction(
            user_id=user_id,
            transaction_type=transaction_type,
            credits=credits,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
            extra_data=extra_data,
        )
        db.add(transaction)
        await db.flush()
        return transaction

    async def get_user_credits_info(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> dict[str, Any]:
        """
        获取用户积分信息

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 积分信息
        """
        subscription = await self.get_or_create_subscription(db, user_id)

        # 获取等级配置
        tier = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier)

        return {
            'user_id': user_id,
            'tier': subscription.tier,
            'tier_display_name': tier.display_name if tier else subscription.tier,
            'current_credits': float(subscription.current_credits),
            'monthly_credits': float(subscription.monthly_credits),
            'used_credits': float(subscription.used_credits),
            'purchased_credits': float(subscription.purchased_credits),
            'billing_cycle_start': subscription.billing_cycle_start.isoformat(),
            'billing_cycle_end': subscription.billing_cycle_end.isoformat(),
            'status': subscription.status,
        }


# 全局实例
credit_service = CreditService()
