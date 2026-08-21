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

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_user_subscription import user_subscription_dao
from backend.app.billing.model import UserSubscription
from backend.app.billing.service import offering_pricing
from backend.app.billing.service.credit_account_service import CREDIT_STATUS_OK
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


#: 合同未显式声明周期长度时的兜底：30 天（与 `credit_grant_service.CYCLE_SECONDS`、
#: NewAPI `CycleSecondsFixed` 同一个数，三处必须一致）。
_FALLBACK_CYCLE_SECONDS = 30 * 24 * 60 * 60


def _parse_rfc3339(value: Any) -> datetime | None:
    """把 NewAPI 的 RFC3339 字符串解析成 datetime；无法解析返回 None（不抛，不猜）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        log.warning(f'[Credit] 权威快照时间字段无法解析，按缺失处理: {value!r}')
        return None


def _sum_credits(subscriptions: list[dict[str, Any]], key: str) -> Decimal | None:
    """把权威快照里某个积分字段求和。

    任何一条解析不出来就整体返回 None——「读不到」必须是一个显式状态，
    半截的和会被当成真实数字展示，比没有更糟。
    """
    total = Decimal(0)
    for item in subscriptions:
        raw = item.get(key)
        if raw is None:
            return None
        try:
            total += Decimal(str(raw))
        except (InvalidOperation, ValueError):
            log.warning(f'[Credit] 权威快照积分字段无法解析，按缺失处理: {key}={raw!r}')
            return None
    return total


def _rolled_forward_cycle(subscription: UserSubscription, now: datetime) -> tuple[datetime, datetime]:
    """按 `cycle_seconds` 把合同周期窗口滚动推进到**包含 now 的那一期**。

    这是「没有权威订阅时」的展示兜底，纯粹是合同自身列上的确定性算术
    （锚点 + 期长），不涉及任何余额或用量——不是第二套账。

    存在的理由：过去这个窗口只在建合同时写一次、之后无人推进（`_refresh_billing_cycle`
    全仓零调用），于是所有用户的「X 月 X 日重置」都定格在建号后 30 天，
    过期几十天仍原样显示。**兜底窗口宁可与权威略有偏差，也不能停在过去。**
    """
    start = subscription.billing_cycle_start or subscription.contract_start_at or now
    cycle_seconds = int(getattr(subscription, 'cycle_seconds', 0) or 0)
    if cycle_seconds <= 0:
        cycle_seconds = _FALLBACK_CYCLE_SECONDS
    period = timedelta(seconds=cycle_seconds)

    if start > now:
        # 合同尚未开始：如实返回首期窗口，不要往回滚。
        return start, start + period
    elapsed = int((now - start).total_seconds() // cycle_seconds)
    cycle_start = start + period * elapsed
    return cycle_start, cycle_start + period


def _resolve_cycle_window(
    subscription: UserSubscription,
    authoritative: dict[str, Any] | None,
    now: datetime,
) -> tuple[datetime, datetime]:
    """定出「当前这一期」的起止：权威优先，缺什么补什么，但**两端必须同源**。

    三种组合，中间那种是升级窗口里真实会出现的：

    1. 权威给了起点也给了重置时刻 → 直接用，最准；
    2. **权威给了起点但没给重置时刻**——NewAPI 尚未升级到带 `next_reset_at` 的版本，
       或该订阅已到合同末期不再重置。此时按权威起点 + 期长推出终点，
       **不能拿合同锚点滚出来的终点去配权威的起点**：两端来自不同锚点，
       会给出「起点 8/17、终点 9/13」这种对不齐的窗口；
    3. 权威侧没有订阅池 → 整段按合同锚点滚动推进。
    """
    rolled_start, rolled_end = _rolled_forward_cycle(subscription, now)
    if authoritative is None:
        return rolled_start, rolled_end

    start = authoritative['start']
    reset_at = authoritative['reset_at']
    if start is None:
        return rolled_start, reset_at or rolled_end
    if reset_at is None:
        cycle_seconds = int(getattr(subscription, 'cycle_seconds', 0) or 0) or _FALLBACK_CYCLE_SECONDS
        return start, start + timedelta(seconds=cycle_seconds)
    return start, reset_at


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

    # `_refresh_billing_cycle` 已删除（2026-08-21）。
    #
    # 它把 `billing_cycle_start/end` 重写成「此刻 → 此刻 +30 天」，但**全仓零调用**——
    # 没有任何端点、任务或 beat 调度触碰过它。于是每份合同的周期窗口在建号时写一次就
    # 永久定格，生产上 4 份合同全部停在 2026-07-18 / 08-10，UI 上那句「7 月 18 日重置」
    # 到 8 月 21 日仍原样显示。
    #
    # 不把它接回去，是因为「按此刻重置周期」本身就是错的：周期边界由合同锚点 +
    # `cycle_seconds` 确定性推导，取决于**谁在什么时候读**的写法会让边界随访问漂移。
    # 现在改由 `_rolled_forward_cycle` 在读路径上确定性推导，权威侧（NewAPI）有订阅时
    # 直接用权威窗口。读路径不再写库，也就没有「谁来触发推进」这个问题。

    @staticmethod
    def _authoritative_cycle(account: dict[str, Any]) -> dict[str, Any] | None:
        """从权威账户快照里取出「当前周期」的四件事：额度、已用、剩余、起止。

        返回 None 表示**权威侧没有可展示的周期**，有三种成因，展示层一律按
        「本档位当前没有周期额度」处理，**绝不用云端的 `monthly_credits` 顶上**——
        那个数字只是合同上的一句话，NewAPI 里没有对应的订阅池时它没有任何执行力，
        拿它当分母就会渲染出「本周期已用 151.59 / 100 积分」这种自相矛盾的进度条：

        1. `credit_status != 'ok'`：读不到权威，此刻**不知道**周期是什么样；
        2. 快照里没有任何订阅：这个账户确实没有周期额度池（只有永久钱包）；
        3. 关键字段解析不出来：同样是「不知道」，不猜。
        """
        if account['credit_status'] != CREDIT_STATUS_OK:
            return None
        subscriptions = account.get('subscriptions') or []
        if not subscriptions:
            return None

        limit = _sum_credits(subscriptions, 'cycle_limit_credits')
        used = _sum_credits(subscriptions, 'cycle_used_credits')
        remaining = _sum_credits(subscriptions, 'cycle_remaining_credits')
        if limit is None or used is None or remaining is None:
            return None

        # 多条订阅并存时取最早的起点与最早的重置时刻：最先到来的那次清零，
        # 就是主人下一次看到额度变化的时刻。
        starts = [dt for dt in (_parse_rfc3339(item.get('cycle_start_at')) for item in subscriptions) if dt]
        resets = [dt for dt in (_parse_rfc3339(item.get('next_reset_at')) for item in subscriptions) if dt]
        return {
            'limit': limit,
            'used': used,
            'remaining': remaining,
            'start': min(starts) if starts else None,
            'reset_at': min(resets) if resets else None,
        }

    async def _free_default_info(self, db: AsyncSession, user_id: int, app_code: str) -> dict[str, Any]:
        """尚无合同时的免费档默认快照（只读，不写库）。

        余额部分照常读 NewAPI 权威——没有合同不代表没有钱包。
        """
        from backend.app.billing.service.credit_account_service import credit_account_service

        tier = await offering_pricing.get_tier(db, 'free')
        account = await credit_account_service.get_account(db, user_id, app_code)
        now = timezone.now()
        cycle = self._authoritative_cycle(account)

        # 没有合同 → 周期窗口只能按「此刻起一期」示意；一旦权威侧有订阅就以权威为准。
        cycle_start = cycle['start'] if cycle and cycle['start'] else now
        cycle_end = (
            cycle['reset_at'] if cycle and cycle['reset_at'] else cycle_start + timedelta(seconds=_FALLBACK_CYCLE_SECONDS)
        )
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
            # 权威侧没有订阅池就是 0——不要拿档位目录里那句「每 30 天 100 积分」当账户事实，
            # 它描述的是**商品**，不是这个账户此刻真的拥有的额度。
            'monthly_credits': float(cycle['limit']) if cycle else 0.0,
            'used_credits': float(cycle['used']) if cycle else 0.0,
            'cycle_consumed_credits': float(cycle['used']) if cycle else 0.0,
            'purchased_credits': float(account['wallet_credits']) if account['wallet_credits'] is not None else 0.0,
            # 读不到就是 None，不是 0。写死的 0 会在 UI 上渲染成「套餐额度 0」这句假话。
            'monthly_remaining': float(cycle['remaining']) if cycle else None,
            'bonus_remaining': None,
            'billing_cycle_start': cycle_start.isoformat(),
            'billing_cycle_end': cycle_end.isoformat(),
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
        current_credits = float(account['available_credits']) if account['available_credits'] is not None else None

        # ===== 当前周期窗口 =====
        # 权威侧有订阅就用权威窗口；没有就按合同锚点 + cycle_seconds **滚动推进**到
        # 包含此刻的那一期。旧写法直接返回 `subscription.billing_cycle_start/end` 两列，
        # 而那两列建合同时写一次之后无人推进（`_refresh_billing_cycle` 全仓零调用），
        # 于是「X 月 X 日重置」永久定格在建号后 30 天。
        authoritative = self._authoritative_cycle(account)
        billing_cycle_start, billing_cycle_end = _resolve_cycle_window(subscription, authoritative, now)

        # ===== 本周期已用 =====
        # 权威侧有订阅池时，直接用它的 cycle_used_credits：那是**同一本账**里的
        # 已用/额度一对，相除必然自洽。旧写法拿「NewAPI 全量日消费」当分子、
        # 云端 monthly_credits 当分母——两本账相除，才会出现 151.59 / 100 这种超额。
        if authoritative:
            cycle_consumed_credits = authoritative['used']
        else:
            cycle = await billing_usage_service.get_cycle_consumed(
                db, user_id, billing_cycle_start, min(now, billing_cycle_end), app_code,
            )
            cycle_consumed_credits = cycle['consumed_credits']

        # ===== 状态按日期重算（修复「过期却显示生效中」）=====
        # status 是存量字段，真实 LLM 走 NewAPI 从不触发内部翻转 → 永远停在上次写入的 'active'。
        # 读取时按合同结束日与 now 比对得出有效状态；免费档无结束日，永不过期。
        effective_status = subscription.status
        sub_end = getattr(subscription, 'subscription_end_date', None)
        if subscription.tier != 'free' and sub_end is not None and now > sub_end:
            effective_status = 'expired'
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
            # 周期额度取权威侧的订阅池上限。合同列 `monthly_credits` 只是商业约定的
            # 一句话，NewAPI 里没有对应订阅时它没有任何执行力——生产上 4 份免费合同
            # 全部 external_subscription_id 为空、从未履约，那个 100 从来就没生效过。
            'monthly_credits': float(authoritative['limit']) if authoritative else 0.0,
            # 用量口径统一由 NewAPI 给出：云端不再有「已用/剩余」的第二份账
            'used_credits': float(cycle_consumed_credits),
            'cycle_consumed_credits': float(cycle_consumed_credits),
            'purchased_credits': float(account['wallet_credits']) if account['wallet_credits'] is not None else 0.0,
            # 读不到就是 None，不是 0——写死的 0 会在 UI 上变成「套餐额度 0」这句假话。
            'monthly_remaining': float(authoritative['remaining']) if authoritative else None,
            'bonus_remaining': None,
            'billing_cycle_start': billing_cycle_start.isoformat(),
            'billing_cycle_end': billing_cycle_end.isoformat(),
            'subscription_start_date': subscription_start.isoformat() if subscription_start else None,
            'subscription_end_date': subscription_end.isoformat() if subscription_end else None,
            'next_grant_date': next_grant_date.isoformat() if next_grant_date else None,
            'status': effective_status,
        }


credit_service = CreditService()
