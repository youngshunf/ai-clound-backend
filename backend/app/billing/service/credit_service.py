"""积分核心服务 - 积分计算、检查和扣除逻辑
@author Ysf
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.crud.crud_user_subscription import user_subscription_dao
from backend.app.billing.model import CreditTransaction, UserCreditBalance, UserSubscription
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
    """积分核心服务

    计费消费侧（按模型费率计算/扣费）原由自建 LLM 网关 gateway.py 承担，已随网关删除
    （2026-06-15 new-api 解耦）；积分扣减改由 new-api 用量对账每小时回扣
    （app/newapi/credit_sync_service.reconcile_all）。本服务只保留积分余额账本原语
    （add_credits / deduct_credits / check_credits / get_total_available_credits）。
    """

    async def get_or_create_subscription(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> UserSubscription:
        """
        获取用户订阅，如果不存在则创建免费订阅

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 用户订阅
        """
        # 查询用户订阅
        subscription = await user_subscription_dao.select_model_by_column(db, user_id=user_id, app_code=app_code)

        if subscription:
            return subscription

        # 创建免费订阅
        log.info(f'[Credit] Creating free subscription for user {user_id}, app_code={app_code}')
        subscription = await self._create_free_subscription(db, user_id, app_code)
        return subscription

    async def _create_free_subscription(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> UserSubscription:
        """创建免费合同（doc94 F1 改造后：只建合同 + 登记履约命令，不写任何余额）。

        免费额度的授予收敛到 `credit_grant_service.ensure_free_contract` 一处，
        幂等键带 policy_version 与 epoch——否则免费政策撤销后再授予会被自己写下的键
        永久挡住，该用户此生再也发不出第二次免费额度。
        """
        from backend.app.billing.service.credit_grant_service import credit_grant_service

        contract = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=app_code)
        if contract is None:
            raise errors.RequestError(msg=f'免费档配置缺失，无法为用户 {user_id} 建立免费合同')
        return contract

    # 最小积分阈值：用户至少需要有这么多积分才能发起请求
    # 这是为了防止零积分用户发起请求后无法扣费的问题
    MIN_CREDIT_THRESHOLD = Decimal('0.1')

    async def check_credits(
        self,
        db: AsyncSession,
        user_id: int,
        estimated_credits: Decimal | None = None,
        app_code: str = 'huanxing',
    ) -> UserSubscription:
        """
        检查用户积分是否足够

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param estimated_credits: 预估需要的积分 (可选)
        :param app_code: 应用标识
        :return: 用户订阅
        :raises SubscriptionNotFoundError: 订阅未找到
        :raises SubscriptionExpiredError: 订阅已过期
        :raises InsufficientCreditsError: 积分不足
        """
        subscription = await self.get_or_create_subscription(db, user_id, app_code)

        # 检查订阅状态
        if subscription.status != 'active':
            raise SubscriptionExpiredError(user_id)

        # 检查计费周期
        now = timezone.now()
        if now > subscription.billing_cycle_end:
            # 尝试刷新周期
            subscription = await self._refresh_billing_cycle(db, subscription)

        # 从 balance 表获取总可用积分
        total_credits = await self.get_total_available_credits(db, user_id, app_code)

        # 检查积分余额
        # 1. 如果指定了预估积分，检查是否足够
        # 2. 即使没有指定预估积分，也要确保用户有最低积分余额
        required_credits = estimated_credits or self.MIN_CREDIT_THRESHOLD
        if total_credits < required_credits:
            raise InsufficientCreditsError(total_credits, required_credits)

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
        app_code: str = 'huanxing',
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
        :param app_code: 应用标识
        :return: 更新后的订阅
        :raises InsufficientCreditsError: 积分不足
        """
        # 获取用户有效的积分余额记录（按过期时间升序，NULL 放最后）
        balances = await self._get_active_balances_for_update(db, user_id, app_code)

        # 计算总可用积分
        total_available = sum(b.remaining_amount for b in balances)

        if total_available < credits:
            raise InsufficientCreditsError(total_available, credits)

        # 获取订阅记录（用于记录交易和更新汇总）
        subscription = await self.get_or_create_subscription(db, user_id, app_code)
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
                remaining_to_deduct = Decimal(0)
            else:
                # 当前记录不够，全部扣完
                remaining_to_deduct -= balance.remaining_amount
                balance.used_amount += balance.remaining_amount
                balance.remaining_amount = Decimal(0)

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
            app_code=app_code,
        )

        log.info(f'[Credit] Deducted {credits} credits from user {user_id} (app={app_code}), '
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
        app_code: str = 'huanxing',
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
        :param app_code: 应用标识
        :return: 更新后的订阅
        """
        subscription = await self.get_or_create_subscription(db, user_id, app_code)

        # 获取当前总积分
        balance_before = await self.get_total_available_credits(db, user_id, app_code)

        # 创建积分余额记录
        if is_purchased:
            credit_type = 'purchased'
            source_type = 'purchase'
        elif transaction_type == 'official_grant':
            credit_type = 'official_grant'
            source_type = 'official_grant'
        else:
            credit_type = 'bonus'
            source_type = 'bonus'
        await self._create_balance_record(
            db,
            user_id=user_id,
            credit_type=credit_type,
            amount=credits,
            expires_at=expires_at,  # 购买的积分 expires_at=None，永不过期
            source_type=source_type,
            source_reference_id=reference_id,
            description=description,
            app_code=app_code,
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
            app_code=app_code,
        )

        log.info(f'[Credit] Added {credits} credits to user {user_id} (app={app_code}), '
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
        app_code = getattr(subscription, 'app_code', 'huanxing') or 'huanxing'

        # 年度订阅用户由定时任务处理，不自动刷新
        subscription_type = getattr(subscription, 'subscription_type', 'monthly') or 'monthly'
        if subscription_type == 'yearly':
            # 检查年度订阅是否已过期
            subscription_end = getattr(subscription, 'subscription_end_date', None)
            now = timezone.now()
            if subscription_end and now > subscription_end:
                subscription.status = 'expired'
                log.info(f'[Credit] Yearly subscription expired for user {subscription.user_id}')
            return subscription

        # 以下是月度订阅的刷新逻辑
        # 获取等级配置
        tier = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier, app_code=app_code)
        monthly_credits = tier.monthly_credits if tier else Decimal(500)  # 默认 500 积分

        # 获取当前总可用积分
        balance_before = await self.get_total_available_credits(db, subscription.user_id, app_code)

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
            app_code=app_code,
        )

        # 更新 subscription 汇总字段（保持兼容性）
        new_total = balance_before + monthly_credits
        subscription.current_credits = new_total
        subscription.used_credits = Decimal(0)  # 重置已使用（仅月度周期内）

        # 记录月度赠送交易
        await self._record_transaction(
            db,
            user_id=subscription.user_id,
            transaction_type='monthly_grant',
            credits=monthly_credits,
            balance_before=balance_before,
            balance_after=new_total,
            description=f'{subscription.tier}版月度赠送积分',
            app_code=app_code,
        )

        log.info(f'[Credit] Refreshed billing cycle for user {subscription.user_id} (app={app_code}), '
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
        app_code: str = 'huanxing',
    ) -> CreditTransaction:
        """记录积分交易"""
        transaction = CreditTransaction(
            app_code=app_code,
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
        app_code: str = 'huanxing',
    ) -> dict[str, Any]:
        """
        获取用户积分信息

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分信息
        """
        subscription = await self.get_or_create_subscription(db, user_id, app_code)

        # 获取等级配置
        tier = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier, app_code=app_code)

        # 从 balance 表获取详细积分信息（有效期内的所有记录，不管是否用完）
        balances = await self.get_user_valid_balances(db, user_id, app_code)
        # 获取有剩余的记录用于计算可用积分
        active_balances = await self.get_user_active_balances(db, user_id, app_code)

        total_credits = sum(b.remaining_amount for b in active_balances)
        total_used = sum(b.used_amount for b in balances)

        # 分类统计（基于有剩余的记录）—— 仅展示「购买/赠送」构成；可用积分以 new-api 为准
        monthly_remaining = sum(b.remaining_amount for b in active_balances if b.credit_type == 'monthly')
        purchased_remaining = sum(b.remaining_amount for b in active_balances if b.credit_type == 'purchased')
        bonus_remaining = sum(b.remaining_amount for b in active_balances if b.credit_type == 'bonus')

        # ===== new-api 权威：可用积分 + 本期真实消耗 =====
        # LLM 网关是 new-api（非内置 litellm）。真实余额=(quota−used_quota)/RATE、真实消耗=logs；
        # 内部 user_credit_balance 不被 new-api 消耗扣减，故可用积分/本月已用必须实时取 new-api，
        # 否则会出现「内部显示 20000 可用、实际已大量消耗」的脱节（零 fake，无映射才回退内部）。
        from backend.app.billing.service.billing_usage_service import billing_usage_service

        now = timezone.now()
        available = await billing_usage_service.get_available_credits(db, user_id, app_code)
        current_credits = available['available_credits'] if available is not None else total_credits

        # 消耗窗口上界取 min(now, 周期结束)：周期内 → 至今消耗；已过期 → 该周期内消耗（不外溢）
        cycle_end = min(now, subscription.billing_cycle_end)
        cycle = await billing_usage_service.get_cycle_consumed(
            db, user_id, subscription.billing_cycle_start, cycle_end, app_code,
        )
        cycle_consumed_credits = cycle['consumed_credits']

        # ===== 状态按日期重算（修复「过期却显示生效中」）=====
        # status 是存量字段，真实 LLM 走 new-api 从不触发内部 check_credits 翻转 → 永远停在
        # 上次升级写入的 'active'。读取时按订阅结束日与 now 比对得出有效状态；免费版无结束日，永不过期。
        effective_status = subscription.status
        sub_end = getattr(subscription, 'subscription_end_date', None)
        if subscription.tier != 'free' and sub_end is not None and now > sub_end:
            effective_status = 'expired'

        return {
            'user_id': user_id,
            'tier': subscription.tier,
            'tier_display_name': tier.display_name if tier else subscription.tier,
            'subscription_type': getattr(subscription, 'subscription_type', 'monthly') or 'monthly',
            'current_credits': float(current_credits),
            'monthly_credits': float(subscription.monthly_credits),
            'used_credits': float(total_used),
            'cycle_consumed_credits': float(cycle_consumed_credits),
            'purchased_credits': float(purchased_remaining),
            'monthly_remaining': float(monthly_remaining),
            'bonus_remaining': float(bonus_remaining),
            'billing_cycle_start': subscription.billing_cycle_start.isoformat(),
            'billing_cycle_end': subscription.billing_cycle_end.isoformat(),
            'subscription_start_date': subscription.subscription_start_date.isoformat() if getattr(subscription, 'subscription_start_date', None) else None,
            'subscription_end_date': subscription.subscription_end_date.isoformat() if getattr(subscription, 'subscription_end_date', None) else None,
            'next_grant_date': subscription.next_grant_date.isoformat() if getattr(subscription, 'next_grant_date', None) else None,
            'status': effective_status,
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
        app_code: str = 'huanxing',
    ) -> UserCreditBalance:
        """创建积分余额记录"""
        balance = UserCreditBalance(
            app_code=app_code,
            user_id=user_id,
            credit_type=credit_type,
            original_amount=amount,
            used_amount=Decimal(0),
            remaining_amount=amount,
            expires_at=expires_at,
            granted_at=timezone.now(),
            source_type=source_type,
            source_reference_id=source_reference_id,
            description=description,
        )
        db.add(balance)
        await db.flush()
        log.info(f'[Credit] Created balance record for user {user_id} (app={app_code}): '
                 f'type={credit_type}, amount={amount}, expires_at={expires_at}')
        return balance

    async def get_total_available_credits(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> Decimal:
        """
        获取用户总可用积分（从 balance 表计算）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 总可用积分
        """
        now = timezone.now()
        stmt = select(func.coalesce(func.sum(UserCreditBalance.remaining_amount), 0)).where(
            and_(
                UserCreditBalance.app_code == app_code,
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
        return Decimal(str(total)) if total else Decimal(0)

    async def get_user_active_balances(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户有效的积分余额记录列表（未过期且有剩余）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.app_code == app_code,
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

    async def get_user_valid_balances(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户有效期内的所有积分余额记录（不管是否用完）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.app_code == app_code,
                    UserCreditBalance.user_id == user_id,
                    or_(
                        UserCreditBalance.expires_at.is_(None),
                        UserCreditBalance.expires_at > now,
                    ),
                )
            )
            .order_by(UserCreditBalance.granted_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_user_expired_balances(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户已过期的积分余额记录（历史记录）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.app_code == app_code,
                    UserCreditBalance.user_id == user_id,
                    UserCreditBalance.expires_at.isnot(None),
                    UserCreditBalance.expires_at <= now,
                )
            )
            .order_by(UserCreditBalance.expires_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def _get_active_balances_for_update(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> Sequence[UserCreditBalance]:
        """
        获取用户有效的积分余额记录并锁定（用于扣除操作）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分余额记录列表
        """
        now = timezone.now()
        stmt = (
            select(UserCreditBalance)
            .where(
                and_(
                    UserCreditBalance.app_code == app_code,
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
