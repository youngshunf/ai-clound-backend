"""积分核心服务 - 积分计算、检查和扣除逻辑
@author Ysf
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.user_tier.crud.crud_credit_transaction import credit_transaction_dao
from backend.app.user_tier.crud.crud_model_credit_rate import model_credit_rate_dao
from backend.app.user_tier.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.user_tier.crud.crud_user_subscription import user_subscription_dao
from backend.app.user_tier.model import CreditTransaction, ModelCreditRate, SubscriptionTier, UserSubscription, UserCreditBalance
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
    # 标准比例: 1M tokens = 输入 5 积分 / 输出 15 积分
    # 即 1K tokens = 输入 0.005 积分 / 输出 0.015 积分
    DEFAULT_BASE_CREDIT_PER_1K = Decimal('1.0')
    DEFAULT_INPUT_MULTIPLIER = Decimal('0.005')
    DEFAULT_OUTPUT_MULTIPLIER = Decimal('0.015')

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
        monthly_credits = free_tier.monthly_credits if free_tier else Decimal('500')  # 默认 500 积分

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

        # 创建积分余额记录
        await self._create_balance_record(
            db,
            user_id=user_id,
            credit_type='monthly',
            amount=monthly_credits,
            expires_at=cycle_end,
            source_type='subscription_grant',
            description='免费版月度赠送积分',
        )

        # 记录月度赠送交易
        await self._record_transaction(
            db,
            user_id=user_id,
            transaction_type='monthly_grant',
            credits=monthly_credits,
            balance_before=Decimal('0'),
            balance_after=monthly_credits,
            description='免费版月度赠送积分',
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

        # 从 balance 表获取总可用积分
        total_credits = await self.get_total_available_credits(db, user_id)

        # 检查积分余额
        if estimated_credits and total_credits < estimated_credits:
            raise InsufficientCreditsError(total_credits, estimated_credits)

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
        按过期时间顺序扣除：先扣即将过期的，购买的积分（永不过期）最后扣

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
        # 获取用户有效的积分余额记录（按过期时间升序，NULL 放最后）
        balances = await self._get_active_balances_for_update(db, user_id)

        # 计算总可用积分
        total_available = sum(b.remaining_amount for b in balances)

        if total_available < credits:
            raise InsufficientCreditsError(total_available, credits)

        # 获取订阅记录（用于记录交易和更新汇总）
        subscription = await self.get_or_create_subscription(db, user_id)
        balance_before = total_available

        # 按顺序从各个 balance 记录中扣除
        remaining_to_deduct = credits
        for balance in balances:
            if remaining_to_deduct <= 0:
                break

            if balance.remaining_amount >= remaining_to_deduct:
                # 当前记录足够扣除
                balance.remaining_amount -= remaining_to_deduct
                balance.used_amount += remaining_to_deduct
                remaining_to_deduct = Decimal('0')
            else:
                # 当前记录不够，全部扣完
                remaining_to_deduct -= balance.remaining_amount
                balance.used_amount += balance.remaining_amount
                balance.remaining_amount = Decimal('0')

        # 更新 subscription 的汇总字段（保持兼容性）
        subscription.current_credits = total_available - credits
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
        expires_at: datetime | None = None,
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
        :param expires_at: 过期时间 (None 表示永不过期)
        :return: 更新后的订阅
        """
        subscription = await self.get_or_create_subscription(db, user_id)

        # 获取当前总积分
        balance_before = await self.get_total_available_credits(db, user_id)

        # 创建积分余额记录
        credit_type = 'purchased' if is_purchased else 'bonus'
        source_type = 'purchase' if is_purchased else 'bonus'
        await self._create_balance_record(
            db,
            user_id=user_id,
            credit_type=credit_type,
            amount=credits,
            expires_at=expires_at,  # 购买的积分 expires_at=None，永不过期
            source_type=source_type,
            source_reference_id=reference_id,
            description=description,
        )

        # 更新 subscription 汇总字段（保持兼容性）
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
            balance_after=balance_before + credits,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
        )

        log.info(f'[Credit] Added {credits} credits to user {user_id}, '
                 f'balance: {balance_before} -> {balance_before + credits}')

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
        monthly_credits = tier.monthly_credits if tier else Decimal('500')  # 默认 500 积分

        # 获取当前总可用积分
        balance_before = await self.get_total_available_credits(db, subscription.user_id)

        now = timezone.now()
        cycle_end = now + timedelta(days=30)

        subscription.billing_cycle_start = now
        subscription.billing_cycle_end = cycle_end
        subscription.monthly_credits = monthly_credits

        # 如果订阅已过期，重新激活
        if subscription.status == 'expired':
            subscription.status = 'active'

        # 创建新的月度积分余额记录
        await self._create_balance_record(
            db,
            user_id=subscription.user_id,
            credit_type='monthly',
            amount=monthly_credits,
            expires_at=cycle_end,
            source_type='subscription_grant',
            description=f'{subscription.tier}版月度赠送积分',
        )

        # 更新 subscription 汇总字段（保持兼容性）
        new_total = balance_before + monthly_credits
        subscription.current_credits = new_total
        subscription.used_credits = Decimal('0')  # 重置已使用（仅月度周期内）

        # 记录月度赠送交易
        await self._record_transaction(
            db,
            user_id=subscription.user_id,
            transaction_type='monthly_grant',
            credits=monthly_credits,
            balance_before=balance_before,
            balance_after=new_total,
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

        # 从 balance 表获取详细积分信息
        balances = await self.get_user_active_balances(db, user_id)
        
        # 老用户迁移：如果没有 balance 记录但 subscription 有积分，自动创建
        if not balances and subscription.current_credits > 0:
            balances = await self._migrate_legacy_credits(db, subscription)
        
        total_credits = sum(b.remaining_amount for b in balances)
        total_used = sum(b.used_amount for b in balances)

        # 分类统计
        monthly_remaining = sum(b.remaining_amount for b in balances if b.credit_type == 'monthly')
        purchased_remaining = sum(b.remaining_amount for b in balances if b.credit_type == 'purchased')
        bonus_remaining = sum(b.remaining_amount for b in balances if b.credit_type == 'bonus')

        return {
            'user_id': user_id,
            'tier': subscription.tier,
            'tier_display_name': tier.display_name if tier else subscription.tier,
            'current_credits': float(total_credits),
            'monthly_credits': float(subscription.monthly_credits),
            'used_credits': float(total_used),
            'purchased_credits': float(purchased_remaining),
            'monthly_remaining': float(monthly_remaining),
            'bonus_remaining': float(bonus_remaining),
            'billing_cycle_start': subscription.billing_cycle_start.isoformat(),
            'billing_cycle_end': subscription.billing_cycle_end.isoformat(),
            'status': subscription.status,
            'balances': [
                {
                    'id': b.id,
                    'credit_type': b.credit_type,
                    'original_amount': float(b.original_amount),
                    'used_amount': float(b.used_amount),
                    'remaining_amount': float(b.remaining_amount),
                    'expires_at': b.expires_at.isoformat() if b.expires_at else None,
                    'granted_at': b.granted_at.isoformat(),
                    'source_type': b.source_type,
                    'description': b.description,
                }
                for b in balances
            ],
        }

    async def _migrate_legacy_credits(
        self,
        db: AsyncSession,
        subscription: UserSubscription,
    ) -> Sequence[UserCreditBalance]:
        """
        迁移老用户积分到 balance 表

        :param db: 数据库会话
        :param subscription: 用户订阅
        :return: 创建的 balance 记录列表
        """
        log.info(f'[Credit] Migrating legacy credits for user {subscription.user_id}')
        balances = []
        now = timezone.now()
        
        # 迁移月度积分（有过期时间）
        monthly_remaining = subscription.current_credits - subscription.purchased_credits
        if monthly_remaining > 0:
            balance = await self._create_balance_record(
                db,
                user_id=subscription.user_id,
                credit_type='monthly',
                amount=monthly_remaining,
                expires_at=subscription.billing_cycle_end,
                source_type='subscription_grant',
                description=f'{subscription.tier}版月度积分（迁移）',
            )
            balances.append(balance)
        
        # 迁移购买积分（永不过期）
        if subscription.purchased_credits > 0:
            balance = await self._create_balance_record(
                db,
                user_id=subscription.user_id,
                credit_type='purchased',
                amount=subscription.purchased_credits,
                expires_at=None,  # 永不过期
                source_type='purchase',
                description='购买积分（迁移）',
            )
            balances.append(balance)
        
        await db.flush()
        return balances

    async def _create_balance_record(
        self,
        db: AsyncSession,
        user_id: int,
        credit_type: str,
        amount: Decimal,
        expires_at: datetime | None,
        source_type: str,
        source_reference_id: str | None = None,
        description: str | None = None,
    ) -> UserCreditBalance:
        """创建积分余额记录"""
        balance = UserCreditBalance(
            user_id=user_id,
            credit_type=credit_type,
            original_amount=amount,
            used_amount=Decimal('0'),
            remaining_amount=amount,
            expires_at=expires_at,
            granted_at=timezone.now(),
            source_type=source_type,
            source_reference_id=source_reference_id,
            description=description,
        )
        db.add(balance)
        await db.flush()
        log.info(f'[Credit] Created balance record for user {user_id}: '
                 f'type={credit_type}, amount={amount}, expires_at={expires_at}')
        return balance

    async def get_total_available_credits(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Decimal:
        """
        获取用户总可用积分（从 balance 表计算）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 总可用积分
        """
        now = timezone.now()
        stmt = select(func.coalesce(func.sum(UserCreditBalance.remaining_amount), 0)).where(
            and_(
                UserCreditBalance.user_id == user_id,
                UserCreditBalance.remaining_amount > 0,
                or_(
                    UserCreditBalance.expires_at.is_(None),
                    UserCreditBalance.expires_at > now,
                ),
            )
        )
        result = await db.execute(stmt)
        total = result.scalar()
        return Decimal(str(total)) if total else Decimal('0')

    async def get_user_active_balances(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户有效的积分余额记录列表（未过期且有剩余）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.user_id == user_id,
                    UserCreditBalance.remaining_amount > 0,
                    or_(
                        UserCreditBalance.expires_at.is_(None),
                        UserCreditBalance.expires_at > now,
                    ),
                )
            )
            # 按过期时间升序，NULL（永不过期）放最后
            .order_by(UserCreditBalance.expires_at.asc().nulls_last())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def _get_active_balances_for_update(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户有效的积分余额记录并锁定（用于扣除操作）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.user_id == user_id,
                    UserCreditBalance.remaining_amount > 0,
                    or_(
                        UserCreditBalance.expires_at.is_(None),
                        UserCreditBalance.expires_at > now,
                    ),
                )
            )
            # 按过期时间升序，NULL（永不过期）放最后
            .order_by(UserCreditBalance.expires_at.asc().nulls_last())
            .with_for_update()
        )
        result = await db.execute(stmt)
        return result.scalars().all()


# 全局实例
credit_service = CreditService()
