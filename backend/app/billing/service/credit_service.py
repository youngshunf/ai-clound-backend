"""订阅读服务（doc94 D1 之后只剩「合同 + 展示」）。

**这个文件曾经是云端的第二套余额账本**：`user_credit_balance` 记余额桶、
`credit_transaction` 记流水，`check_credits`/`deduct_credits`/`add_credits` 直接改余额，
再由每小时的同步任务把结果覆盖写回 NewAPI。两套账本 + 反向覆盖，正是
「套餐 100、本月已用 487、仍可继续请求」的根因。

**D1 之后**：余额与消费的唯一权威是 NewAPI，云端不再持有余额表与流水表，
也不再有任何加减余额的原语。这里只剩两件事：

1. 维护**订阅合同**（谁在哪一档、周期起止）——那是商业事实，本来就该在云端；
2. 组装**展示用**的订阅信息，余额部分原样透传 NewAPI 的权威快照。

任何「在云端算一个余额」的函数都不应该回到这个文件。
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_user_subscription import user_subscription_dao
from backend.app.billing.model import UserSubscription
from backend.app.billing.service import offering_pricing
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone


class SubscriptionNotFoundError(errors.HTTPError):
    """订阅未找到错误"""

    def __init__(self, user_id: int) -> None:
        super().__init__(code=404, msg=f'Subscription not found for user: {user_id}')


class SubscriptionExpiredError(errors.HTTPError):
    """订阅已过期错误"""

    def __init__(self, user_id: int) -> None:
        super().__init__(code=403, msg=f'Subscription expired for user: {user_id}')


class CreditService:
    """订阅合同读服务。**不持有任何余额原语**（doc94 D1）。"""

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

        # 以下是月度订阅的周期推进。
        # **只推进周期与档位快照，不发任何额度**：周期额度的重置由 NewAPI 按
        # 30 天固定周期自己完成（doc94 N2）。云端在这里发一次额度，就等于
        # 又开了一条绕过权威的发放路径。
        tier = await offering_pricing.get_tier(db, subscription.tier) if subscription.tier else None
        credits_per_cycle = tier.credits_per_cycle if tier else subscription.monthly_credits

        now = timezone.now()
        subscription.billing_cycle_start = now
        subscription.billing_cycle_end = now + timedelta(days=30)
        subscription.monthly_credits = credits_per_cycle

        # 订阅已过期时重新激活（合同仍在有效期内才会走到这里）
        if subscription.status == 'expired':
            subscription.status = 'active'

        log.info(
            f'[Credit] 推进计费周期 user={subscription.user_id} app={app_code} '
            f'tier={subscription.tier} credits_per_cycle={credits_per_cycle}（额度由 NewAPI 重置）'
        )

        return subscription

    async def _free_default_info(self, db: AsyncSession, user_id: int, app_code: str) -> dict[str, Any]:
        """尚无合同时的免费档默认快照（只读，不写库）。

        余额部分照常读 NewAPI 权威——没有合同不代表没有钱包。
        """
        from backend.app.billing.service.credit_account_service import credit_account_service

        tier = await offering_pricing.get_tier(db, 'free')
        account = await credit_account_service.get_account(db, user_id, app_code)
        now = timezone.now()

        return {
            'user_id': user_id,
            'tier': 'free',
            'tier_display_name': tier.display_name if tier else 'free',
            'subscription_type': 'monthly',
            'current_credits': float(account['available_credits']) if account['available_credits'] is not None else None,
            'credit_status': account['credit_status'],
            'measured_at': account['measured_at'],
            'wallet_credits': account['wallet_credits'],
            'newapi_subscriptions': account['subscriptions'],
            'monthly_credits': float(tier.credits_per_cycle) if tier else 0.0,
            'used_credits': 0.0,
            'cycle_consumed_credits': 0.0,
            'purchased_credits': float(account['wallet_credits']) if account['wallet_credits'] is not None else 0.0,
            'monthly_remaining': 0.0,
            'bonus_remaining': 0.0,
            'billing_cycle_start': now.isoformat(),
            'billing_cycle_end': (now + timedelta(days=30)).isoformat(),
            'subscription_start_date': None,
            'subscription_end_date': None,
            'next_grant_date': None,
            'status': 'active',
        }

    async def get_user_credits_info(
        self,
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> dict[str, Any]:
        """
        获取用户积分信息

        **这是纯读路径，不建合同**：过去它会顺手建一份免费合同，于是「打开账单页」
        变成了一次写操作——用户还没有 NewAPI 账户时直接 500。建免费合同属于开通流程
        （`credit_grant_service.ensure_free_contract`），不该由一次 GET 触发。
        没有合同时按免费档默认快照返回。

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param app_code: 应用标识
        :return: 积分信息
        """
        subscription = await user_subscription_dao.select_model_by_column(db, user_id=user_id, app_code=app_code)
        if not isinstance(subscription, UserSubscription):
            return await self._free_default_info(db, user_id, app_code)

        # 档位展示名取自商品目录（doc94 D1：subscription_tier 已删）
        tier = await offering_pricing.get_tier(db, subscription.tier) if subscription.tier else None

        # ===== NewAPI 权威账户：可用积分 + 当前周期用量（doc94 F3）=====
        # 绝不再用 (quota − used_quota) 推算——users.quota 本身就是当前剩余额度，
        # used_quota 是累计用量，两者相减会算出负数或错误进度。
        # 读不到就如实说读不到：余额字段为 None、credit_status=unavailable，
        # 既不回落云端旧值，也不伪造 0。
        from backend.app.billing.service.billing_usage_service import billing_usage_service
        from backend.app.billing.service.credit_account_service import credit_account_service

        now = timezone.now()
        account = await credit_account_service.get_account(db, user_id, app_code)
        current_credits: float | None
        if account['available_credits'] is not None:
            current_credits = float(account['available_credits'])
        else:
            current_credits = None

        # 消耗窗口上界取 min(now, 周期结束)：周期内 → 至今消耗；已过期 → 该周期内消耗（不外溢）
        cycle_end = min(now, subscription.billing_cycle_end)
        cycle = await billing_usage_service.get_cycle_consumed(
            db, user_id, subscription.billing_cycle_start, cycle_end, app_code,
        )
        cycle_consumed_credits = cycle['consumed_credits']

        # ===== 状态按日期重算（修复「过期却显示生效中」）=====
        # status 是存量字段，真实 LLM 走 NewAPI 从不触发内部翻转 → 永远停在上次写入的 'active'。
        # 读取时按合同结束日与 now 比对得出有效状态；免费档无结束日，永不过期。
        effective_status = subscription.status
        sub_end = getattr(subscription, 'subscription_end_date', None)
        if subscription.tier != 'free' and sub_end is not None and now > sub_end:
            effective_status = 'expired'
        billing_cycle_start = subscription.billing_cycle_start
        billing_cycle_end = subscription.billing_cycle_end
        if billing_cycle_start is None or billing_cycle_end is None:
            raise errors.ServerError(msg='订阅计费周期不完整')
        subscription_start = subscription.subscription_start_date
        subscription_end = subscription.subscription_end_date
        next_grant_date = subscription.next_grant_date

        return {
            'user_id': user_id,
            'tier': subscription.tier,
            'tier_display_name': tier.display_name if tier else subscription.tier,
            'subscription_type': getattr(subscription, 'subscription_type', 'monthly') or 'monthly',
            'current_credits': current_credits,
            # 权威读状态与测量时刻：展示层据此三态渲染，并显示「数据更新于 X 前」。
            'credit_status': account['credit_status'],
            'measured_at': account['measured_at'],
            'wallet_credits': account['wallet_credits'],
            'newapi_subscriptions': account['subscriptions'],
            'monthly_credits': float(subscription.monthly_credits),
            # 用量口径统一由 NewAPI 给出：云端不再有「已用/剩余」的第二份账
            'used_credits': float(cycle_consumed_credits),
            'cycle_consumed_credits': float(cycle_consumed_credits),
            'purchased_credits': float(account['wallet_credits']) if account['wallet_credits'] is not None else 0.0,
            'monthly_remaining': 0.0,
            'bonus_remaining': 0.0,
            'billing_cycle_start': billing_cycle_start.isoformat(),
            'billing_cycle_end': billing_cycle_end.isoformat(),
            'subscription_start_date': subscription_start.isoformat() if subscription_start else None,
            'subscription_end_date': subscription_end.isoformat() if subscription_end else None,
            'next_grant_date': next_grant_date.isoformat() if next_grant_date else None,
            'status': effective_status,
        }


credit_service = CreditService()
