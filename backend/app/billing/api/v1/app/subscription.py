"""用户订阅 API - 面向前端用户（JWT 认证）

路径前缀: /api/v1/user_tier/app/subscription
认证方式: JWT（从 token 获取 user_id）

@author Ysf
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend.app.billing.service import offering_pricing
from backend.app.billing.service.credit_service import credit_service
from backend.app.billing.service.credit_usage_service import credit_usage_service
from backend.common.pagination import DependsPagination, PageData, paging_data
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession
from backend.utils.timezone import timezone

router = APIRouter()


# ==================== Response Schemas ====================


class CreditUsageItem(BaseModel):
    """一条 LLM 消费流水（NewAPI 权威，云端不做金额算术）"""

    id: int
    created_at: int = Field(description='消费时刻 Unix 秒')
    model_name: str = ''
    token_name: str = ''
    credits: str = Field(description='本次消费的积分数（十进制字符串，正数表示消耗）')
    prompt_tokens: int = 0
    completion_tokens: int = 0
    use_time: int = 0
    is_stream: bool = False
    funding_source: str | None = Field(None, description='资金来源 subscription/wallet/composite；历史日志可能为空')


class CreditUsagePage(BaseModel):
    """消费流水分页。

    `usage_status` 区分「读到了」「读不到」「还没开通账户」——
    绝不用空列表把「读不到」伪装成「没有消费」。
    """

    usage_status: str = Field(description='ok / unavailable / unmapped')
    items: list[CreditUsageItem] = []
    total: int = 0
    page: int = 1
    size: int = 20
    measured_at: str | None = Field(None, description='NewAPI 的测量时刻，展示侧据此显示新鲜度')
    unavailable_reason: str | None = None


class CreditDailyItem(BaseModel):
    """积分流水「按日聚合」项（按 Asia/Shanghai 本地日）。

    **四类额度变动各自成列，展示层必须分行渲染，不要相加。** 它们金额可以互相抵消，
    合并成一个数之后主人就看不出当天到底发生了什么：生产实测 2026-08-21 那天
    同时有「免费档发 100 / 升级清零 91.68 / 轻享版发 500 / 积分包 +200 / 消耗 45.53」，
    旧口径只显示一个绿色的「+154.47」，消耗和清零全被吃掉了。
    """
    date: str = Field(description='日期 YYYY-MM-DD（Asia/Shanghai 本地日）')
    consumed: Decimal = Field(description='当日 LLM 消耗合计（≤0）')
    subscription_granted: Decimal = Field(default=Decimal(0), description='当日订阅周期额度发放（≥0）')
    subscription_revoked: Decimal = Field(default=Decimal(0), description='当日订阅额度清零/回收（≤0，如升级换档）')
    pack_granted: Decimal = Field(default=Decimal(0), description='当日永久积分入账（≥0：买积分包/赠送/活动）')
    pack_revoked: Decimal = Field(default=Decimal(0), description='当日永久积分回收（≤0：退款）')
    granted: Decimal = Field(description='当日入账合计（订阅 + 积分包，≥0）')
    net: Decimal = Field(description='当日净变动（四类变动 + 消耗）')
    count: int = Field(description='当日流水笔数')
    request_count: int = Field(default=0, description='当日 LLM 请求次数（usage 交易笔数）')
    token_count: int = Field(default=0, description='当日消耗 token 数（usage 交易 input+output 累加）')


class CreditDailyPage(BaseModel):
    """流水按日聚合分页（字段与通用 PageData 对齐，省去 links）"""
    items: list[CreditDailyItem] = []
    total: int = Field(description='总天数')
    page: int
    size: int
    total_pages: int
    usage_status: str = Field('ok', description='消费侧读取状态 ok / unavailable / unmapped')
    measured_at: str | None = Field(None, description='NewAPI 的测量时刻')


class SubscriptionInfoResponse(BaseModel):
    """订阅信息响应"""
    user_id: int
    tier: str
    tier_display_name: str | None = None
    subscription_type: str = 'monthly'
    monthly_credits: Decimal
    # current_credits 可为空：读不到 NewAPI 权威余额时如实留空，
    # 既不回落云端旧值，也不伪造 0——前者让用户按过时数字决策，后者让用户以为额度用光了。
    current_credits: Decimal | None = None
    credit_status: str = Field(default='ok', description='权威余额读取状态 ok/unavailable/unmapped')
    measured_at: datetime | None = Field(default=None, description='NewAPI 测量时刻，展示层据此判断新鲜度')
    wallet_credits: Decimal | None = Field(default=None, description='永久钱包剩余积分（NewAPI 权威）')
    newapi_subscriptions: list[dict] = Field(default_factory=list, description='NewAPI 当前订阅周期快照')
    used_credits: Decimal
    cycle_consumed_credits: Decimal = Field(default=Decimal(0), description='本计费周期真实消耗积分（new-api logs 权威）')
    purchased_credits: Decimal
    monthly_remaining: Decimal | None = None
    bonus_remaining: Decimal | None = None
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    subscription_start_date: datetime | None = None
    subscription_end_date: datetime | None = None
    next_grant_date: datetime | None = None
    status: str


class UpgradeSubscriptionRequest(BaseModel):
    """升级订阅请求"""
    tier_name: str = Field(description='目标订阅等级')
    subscription_type: str = Field(default='monthly', description='订阅类型 (monthly/yearly)')


class CalculateUpgradePriceRequest(BaseModel):
    """计算升级价格请求"""
    tier_name: str = Field(description='目标订阅等级')
    subscription_type: str = Field(default='monthly', description='订阅类型 (monthly/yearly)')


class UpgradePriceResult(BaseModel):
    """升级价格计算结果"""
    can_upgrade: bool = Field(description='是否可以升级')
    message: str = Field(description='提示信息')
    target_tier: str = Field(description='目标等级')
    target_tier_display: str = Field(description='目标等级显示名')
    subscription_type: str = Field(description='订阅类型')
    original_price: Decimal = Field(description='原价')
    remaining_value: Decimal = Field(description='当前订阅剩余价值')
    final_price: Decimal = Field(description='实际支付价格')
    remaining_days: int = Field(description='当前订阅剩余天数')
    current_tier: str = Field(description='当前等级')
    current_subscription_type: str = Field(description='当前订阅类型')


class PurchaseCreditsRequest(BaseModel):
    """购买积分包请求"""
    package_id: int = Field(description='积分包 ID')


class PaymentResult(BaseModel):
    """支付结果"""
    success: bool
    order_id: str
    message: str
    new_credits: Decimal | None = None
    new_tier: str | None = None


# ==================== APIs ====================


@router.get(
    '/info',
    summary='获取当前用户订阅信息',
    description='获取当前登录用户的订阅和积分信息',
    dependencies=[DependsJwtAuth],
)
async def get_my_subscription_info(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[SubscriptionInfoResponse]:
    """获取订阅信息"""
    user_id = request.user.id
    app_code = request.state.app_code

    info = await credit_service.get_user_credits_info(db, user_id, app_code)

    data = SubscriptionInfoResponse(
        user_id=info['user_id'],
        tier=info['tier'],
        tier_display_name=info['tier_display_name'],
        subscription_type=info.get('subscription_type', 'monthly'),
        monthly_credits=Decimal(str(info['monthly_credits'])),
        current_credits=Decimal(str(info['current_credits'])) if info.get('current_credits') is not None else None,
        credit_status=info.get('credit_status', 'ok'),
        measured_at=datetime.fromisoformat(info['measured_at'].replace('Z', '+00:00'))
        if info.get('measured_at')
        else None,
        wallet_credits=Decimal(str(info['wallet_credits'])) if info.get('wallet_credits') is not None else None,
        newapi_subscriptions=info.get('newapi_subscriptions', []),
        used_credits=Decimal(str(info['used_credits'])),
        cycle_consumed_credits=Decimal(str(info.get('cycle_consumed_credits', 0))),
        purchased_credits=Decimal(str(info['purchased_credits'])),
        # 这两个可为空：读不到权威周期时如实留空。**不要在这里补 0**——
        # 展示层会把它渲染成「套餐额度 0」，那是一句假话而不是一个缺失值。
        monthly_remaining=Decimal(str(info['monthly_remaining'])) if info.get('monthly_remaining') is not None else None,
        bonus_remaining=Decimal(str(info['bonus_remaining'])) if info.get('bonus_remaining') is not None else None,
        billing_cycle_start=datetime.fromisoformat(info['billing_cycle_start']),
        billing_cycle_end=datetime.fromisoformat(info['billing_cycle_end']),
        subscription_start_date=datetime.fromisoformat(info['subscription_start_date']) if info.get('subscription_start_date') else None,
        subscription_end_date=datetime.fromisoformat(info['subscription_end_date']) if info.get('subscription_end_date') else None,
        next_grant_date=datetime.fromisoformat(info['next_grant_date']) if info.get('next_grant_date') else None,
        status=info['status'],
    )

    return response_base.success(data=data)


@router.get(
    '/transactions',
    summary='获取消费流水（NewAPI 权威）',
    description='分页获取当前用户的 LLM 消费流水；金额由 NewAPI 换算成积分，云端原样透传',
    dependencies=[DependsJwtAuth],
)
async def get_credit_transactions(
    request: Request,
    db: CurrentSession,
    page: Annotated[int, Query(ge=1, description='页码')] = 1,
    size: Annotated[int, Query(ge=1, le=100, description='每页条数')] = 20,
) -> ResponseSchemaModel[CreditUsagePage]:
    """消费流水（doc94 D1）。

    过去这里读云端 `credit_transaction`——一张每小时才同步一次的影子流水表，
    金额还用云端自己的换算常量算。现在只有 NewAPI 一个来源。

    NewAPI 读不到时返回 `usage_status='unavailable'` 且列表为空，**不伪造「没有消费」**：
    把「读不到」显示成「这段时间没花钱」，比直接报错更容易让人做错判断。
    """
    result = await credit_usage_service.list_usage(
        db, request.user.id, page=page, size=size, app_code=request.state.app_code
    )
    return response_base.success(data=CreditUsagePage(**result))


@router.get(
    '/transactions/daily',
    summary='获取流水（按日聚合）',
    description='按本地日（Asia/Shanghai）聚合：消费取 NewAPI 权威，入账取云端履约事件',
    dependencies=[DependsJwtAuth],
)
async def get_credit_transactions_daily(
    request: Request,
    db: CurrentSession,
    page: Annotated[int, Query(ge=1, description='页码')] = 1,
    size: Annotated[int, Query(ge=1, le=100, description='每页天数')] = 20,
) -> ResponseSchemaModel[CreditDailyPage]:
    """按日聚合流水（doc94 D1）。

    **消费**取 NewAPI（唯一计量权威），**入账**取云端履约事件 `credit_grant_event`
    ——那是云端权威的「发了什么」，不是余额表。日边界交给 NewAPI 按展示时区切分，
    避免两侧各切各的日、同一笔消费落在两个日期上。
    """
    result = await credit_usage_service.daily_flow(
        db, request.user.id, page=page, size=size, app_code=request.state.app_code
    )
    return response_base.success(
        data=CreditDailyPage(
            items=[CreditDailyItem(**item) for item in result['items']],
            total=result['total'],
            page=result['page'],
            size=result['size'],
            total_pages=result['total_pages'],
            usage_status=result['usage_status'],
            measured_at=result['measured_at'],
        )
    )


def _calculate_remaining_value(
    current_price: Decimal,
    subscription_end_date: datetime | None,
    subscription_type: str,
) -> tuple[Decimal, int]:
    """计算当前订阅的剩余价值"""
    if not subscription_end_date or current_price <= 0:
        return Decimal(0), 0

    now = timezone.now()
    if now >= subscription_end_date:
        return Decimal(0), 0

    remaining_days = (subscription_end_date - now).days
    total_days = 365 if subscription_type == 'yearly' else 30

    remaining_value = current_price * Decimal(str(remaining_days)) / Decimal(str(total_days))
    remaining_value = remaining_value.quantize(Decimal('0.01'))

    return remaining_value, remaining_days


@router.post(
    '/upgrade/calculate',
    summary='计算升级价格',
    description='计算升级到目标等级需要支付的价格（折算剩余价值）',
    dependencies=[DependsJwtAuth],
)
async def calculate_upgrade_price(
    request: Request,
    db: CurrentSession,
    body: CalculateUpgradePriceRequest,
) -> ResponseSchemaModel[UpgradePriceResult]:
    """计算升级价格"""
    user_id = request.user.id
    app_code = request.state.app_code

    # doc94 D1：档位配置的唯一事实源是商品目录 billing_plan，不再读 subscription_tier。
    target_tier = await offering_pricing.get_tier(db, body.tier_name)
    if not target_tier:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message=f'订阅等级 {body.tier_name} 不存在或未启用',
            target_tier=body.tier_name,
            target_tier_display=body.tier_name,
            subscription_type=body.subscription_type,
            original_price=Decimal(0),
            remaining_value=Decimal(0),
            final_price=Decimal(0),
            remaining_days=0,
            current_tier='',
            current_subscription_type='',
        ))

    subscription = await credit_service.get_or_create_subscription(db, user_id, app_code)
    current_subscription_type = getattr(subscription, 'subscription_type', 'monthly') or 'monthly'

    current_tier_config = await offering_pricing.get_tier(db, subscription.tier) if subscription.tier else None
    current_price = Decimal(0)
    if current_tier_config:
        if current_subscription_type == 'yearly' and current_tier_config.yearly_price:
            current_price = current_tier_config.yearly_price
        else:
            current_price = current_tier_config.monthly_price or Decimal(0)

    if subscription.tier == body.tier_name and current_subscription_type == body.subscription_type:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message=f'您已经是 {target_tier.display_name} {"年度" if body.subscription_type == "yearly" else "月度"}用户',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal(0),
            remaining_value=Decimal(0),
            final_price=Decimal(0),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    if current_subscription_type == 'yearly' and body.subscription_type == 'monthly':
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message='年度订阅用户不能切换为月度订阅',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal(0),
            remaining_value=Decimal(0),
            final_price=Decimal(0),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    target_price_monthly = target_tier.monthly_price or Decimal(0)
    current_price_monthly = (current_tier_config.monthly_price or Decimal(0)) if current_tier_config else Decimal(0)
    if target_price_monthly < current_price_monthly:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message='不支持降级订阅',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal(0),
            remaining_value=Decimal(0),
            final_price=Decimal(0),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    if body.subscription_type == 'yearly' and not target_tier.yearly_price:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message=f'{target_tier.display_name} 暂不支持年度订阅',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal(0),
            remaining_value=Decimal(0),
            final_price=Decimal(0),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    original_price = (
        target_tier.yearly_price if body.subscription_type == 'yearly' else target_tier.monthly_price
    ) or Decimal(0)

    subscription_end = getattr(subscription, 'subscription_end_date', None)
    remaining_value, remaining_days = _calculate_remaining_value(
        current_price,
        subscription_end,
        current_subscription_type,
    )

    final_price = original_price - remaining_value
    if final_price < 0:
        final_price = Decimal(0)

    return response_base.success(data=UpgradePriceResult(
        can_upgrade=True,
        message='',
        target_tier=body.tier_name,
        target_tier_display=target_tier.display_name,
        subscription_type=body.subscription_type,
        original_price=original_price,
        remaining_value=remaining_value,
        final_price=final_price,
        remaining_days=remaining_days,
        current_tier=subscription.tier,
        current_subscription_type=current_subscription_type,
    ))


@router.post(
    '/upgrade',
    summary='升级订阅（已退役）',
    description='模拟支付入口已退役：升级必须走真实下单与支付回调履约',
    dependencies=[DependsJwtAuth],
    status_code=status.HTTP_410_GONE,
)
async def upgrade_subscription() -> ResponseModel:
    """已退役（doc94 P0）：不经真实支付即可改订阅的模拟入口。

    这个端点原本直接改订阅等级并在云端发积分，绕过了支付与履约。订阅变更现在必须走
    「下单 → 第三方支付回调验签 → 履约事件 → NewAPI 订阅池」这一条链路，
    支付状态与履约状态分别可观察，重复通知与超时都不会重复发放。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            'code': 'billing_endpoint_retired',
            'message': '该入口已退役，请通过统一下单接口完成真实支付后由履约事件生效',
        },
    )


@router.post(
    '/purchase',
    summary='购买积分包（已退役）',
    description='模拟支付入口已退役：积分包购买必须走真实下单与支付回调履约',
    dependencies=[DependsJwtAuth],
    status_code=status.HTTP_410_GONE,
)
async def purchase_credits() -> ResponseModel:
    """已退役（doc94 P0）：summary 里写着「模拟支付」的积分包购买入口。

    它把模拟成功当成真实购买成功，直接在云端加余额。积分包购买现在必须走统一下单 API，
    支付成功后由 `wallet_grant` 履约事件把额度增量记进 NewAPI 永久钱包。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            'code': 'billing_endpoint_retired',
            'message': '该入口已退役，请通过统一下单接口完成真实支付后由履约事件生效',
        },
    )
