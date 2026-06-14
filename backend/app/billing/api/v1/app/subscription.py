"""用户订阅 API - 面向前端用户（JWT 认证）

路径前缀: /api/v1/user_tier/app/subscription
认证方式: JWT（从 token 获取 user_id）

@author Ysf
"""

from datetime import timedelta
from typing import Annotated
import uuid

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from decimal import Decimal
from datetime import datetime

from backend.app.billing.service.credit_service import credit_service
from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.crud.crud_credit_package import credit_package_dao
from backend.app.billing.crud.crud_credit_transaction import credit_transaction_dao
from backend.common.pagination import DependsPagination, PageData, paging_data
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.log import log
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.utils.timezone import timezone

router = APIRouter()


# ==================== Response Schemas ====================


class CreditBalanceItem(BaseModel):
    """积分余额项"""
    id: int
    credit_type: str
    original_amount: Decimal
    used_amount: Decimal
    remaining_amount: Decimal
    expires_at: datetime | None = None
    granted_at: datetime
    source_type: str
    description: str | None = None


class CreditTransactionItem(BaseModel):
    """用户端积分流水项（含 LLM 消耗 usage 类）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_type: str = Field(description='交易类型 usage/purchase/refund/monthly_grant/subscription_grant/bonus/adjustment')
    credits: Decimal = Field(description='积分变动数量（正=入账，负=消耗）')
    balance_before: Decimal
    balance_after: Decimal
    reference_id: str | None = None
    reference_type: str | None = Field(None, description='关联类型 llm_usage/payment/pay_order/system')
    description: str | None = None
    extra_data: dict | None = None
    created_time: datetime


class CreditDailyItem(BaseModel):
    """积分流水「按日聚合」项（按 Asia/Shanghai 本地日）"""
    date: str = Field(description='日期 YYYY-MM-DD（Asia/Shanghai 本地日）')
    consumed: Decimal = Field(description='当日消耗合计（≤0）')
    granted: Decimal = Field(description='当日入账合计（≥0）')
    net: Decimal = Field(description='当日净变动')
    count: int = Field(description='当日流水笔数')
    request_count: int = Field(default=0, description='当日 LLM 请求次数（usage 交易笔数）')
    token_count: int = Field(default=0, description='当日消耗 token 数（usage 交易 input+output 累加）')


class CreditDailyPage(BaseModel):
    """积分流水按日聚合分页（字段与通用 PageData 对齐，省去 links）"""
    items: list[CreditDailyItem] = []
    total: int = Field(description='总天数')
    page: int
    size: int
    total_pages: int


class SubscriptionInfoResponse(BaseModel):
    """订阅信息响应"""
    user_id: int
    tier: str
    tier_display_name: str | None = None
    subscription_type: str = 'monthly'
    monthly_credits: Decimal
    current_credits: Decimal
    used_credits: Decimal
    cycle_consumed_credits: Decimal = Field(default=Decimal('0'), description='本计费周期真实消耗积分（new-api logs 权威）')
    purchased_credits: Decimal
    monthly_remaining: Decimal | None = None
    bonus_remaining: Decimal | None = None
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    subscription_start_date: datetime | None = None
    subscription_end_date: datetime | None = None
    next_grant_date: datetime | None = None
    status: str
    balances: list[CreditBalanceItem] = []


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

    balances = [
        CreditBalanceItem(
            id=b['id'],
            credit_type=b['credit_type'],
            original_amount=Decimal(str(b['original_amount'])),
            used_amount=Decimal(str(b['used_amount'])),
            remaining_amount=Decimal(str(b['remaining_amount'])),
            expires_at=datetime.fromisoformat(b['expires_at']) if b['expires_at'] else None,
            granted_at=datetime.fromisoformat(b['granted_at']),
            source_type=b['source_type'],
            description=b['description'],
        )
        for b in info.get('balances', [])
    ]

    data = SubscriptionInfoResponse(
        user_id=info['user_id'],
        tier=info['tier'],
        tier_display_name=info['tier_display_name'],
        subscription_type=info.get('subscription_type', 'monthly'),
        monthly_credits=Decimal(str(info['monthly_credits'])),
        current_credits=Decimal(str(info['current_credits'])),
        used_credits=Decimal(str(info['used_credits'])),
        cycle_consumed_credits=Decimal(str(info.get('cycle_consumed_credits', 0))),
        purchased_credits=Decimal(str(info['purchased_credits'])),
        monthly_remaining=Decimal(str(info.get('monthly_remaining', 0))),
        bonus_remaining=Decimal(str(info.get('bonus_remaining', 0))),
        billing_cycle_start=datetime.fromisoformat(info['billing_cycle_start']),
        billing_cycle_end=datetime.fromisoformat(info['billing_cycle_end']),
        subscription_start_date=datetime.fromisoformat(info['subscription_start_date']) if info.get('subscription_start_date') else None,
        subscription_end_date=datetime.fromisoformat(info['subscription_end_date']) if info.get('subscription_end_date') else None,
        next_grant_date=datetime.fromisoformat(info['next_grant_date']) if info.get('next_grant_date') else None,
        status=info['status'],
        balances=balances,
    )

    return response_base.success(data=data)


@router.get(
    '/balances/history',
    summary='获取历史积分记录',
    description='获取已过期的积分余额记录',
    dependencies=[DependsJwtAuth],
)
async def get_credit_balance_history(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[list[CreditBalanceItem]]:
    """获取历史积分记录"""
    user_id = request.user.id
    app_code = request.state.app_code

    expired_balances = await credit_service.get_user_expired_balances(db, user_id, app_code)

    items = [
        CreditBalanceItem(
            id=b.id,
            credit_type=b.credit_type,
            original_amount=b.original_amount,
            used_amount=b.used_amount,
            remaining_amount=b.remaining_amount,
            expires_at=b.expires_at,
            granted_at=b.granted_at,
            source_type=b.source_type,
            description=b.description,
        )
        for b in expired_balances
    ]

    return response_base.success(data=items)


@router.get(
    '/transactions',
    summary='获取积分流水（含 LLM 消耗）',
    description='分页获取当前用户的积分流水（充值/赠送/月度发放/消耗），按时间倒序，强制数据隔离',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_credit_transactions(
    request: Request,
    db: CurrentSession,
    transaction_type: Annotated[str | None, Query(description='交易类型筛选 usage/purchase/...')] = None,
    reference_type: Annotated[str | None, Query(description='关联类型筛选 llm_usage/payment/...')] = None,
) -> ResponseSchemaModel[PageData[CreditTransactionItem]]:
    """用户端积分流水（含 LLM 消耗 usage 类，reference_type='llm_usage'）。"""
    user_id = request.user.id
    app_code = request.state.app_code

    select_stmt = await credit_transaction_dao.get_select_by_user(
        user_id=user_id,
        app_code=app_code,
        transaction_type=transaction_type,
        reference_type=reference_type,
    )
    page_data = await paging_data(db, select_stmt)
    return response_base.success(data=page_data)


@router.get(
    '/transactions/daily',
    summary='获取积分流水（按日聚合）',
    description='按本地日（Asia/Shanghai）聚合当前用户的积分流水，返回每日消耗/入账/净额/笔数/请求次数/消耗token数，分页倒序；用于流水列表，避免逐条 LLM 请求',
    dependencies=[DependsJwtAuth],
)
async def get_credit_transactions_daily(
    request: Request,
    db: CurrentSession,
    page: Annotated[int, Query(ge=1, description='页码')] = 1,
    size: Annotated[int, Query(ge=1, le=100, description='每页天数')] = 20,
) -> ResponseSchemaModel[CreditDailyPage]:
    """用户端积分流水按日聚合 —— **合并口径**（new-api 消耗 + 内部发放/购买）。

    消耗（请求/token/积分）取自 **new-api logs**（真实 LLM 计量，权威）；入账（月度发放/
    购买/退款）取自内部 credit_transaction。两源在 Python 里按本地日合并、倒序、分页
    （在合并结果上分页，避免日边界被切断）。内部 usage 行已废弃、不参与消耗（零重复计）。
    """
    user_id = request.user.id
    app_code = request.state.app_code

    from backend.app.billing.service.billing_usage_service import billing_usage_service

    # 1) 内部账本：发放/购买/退款（入账，正向）。get_daily_aggregate 的 granted 即正向合计。
    stmt = await credit_transaction_dao.get_daily_aggregate_by_user(user_id=user_id, app_code=app_code)
    internal_rows = (await db.execute(stmt)).all()

    # 2) new-api 权威：本地日真实消耗（负向）+ 请求数 + token 数（10 年窗口足够覆盖账户历史）。
    now = timezone.now()
    consumed_by_day = await billing_usage_service.get_daily_consumed(
        db, user_id, now - timedelta(days=3650), now, app_code,
    )

    # 3) 按本地日合并：入账取内部、消耗/请求/token 取 new-api（new-api 为消耗权威，不叠加内部 usage）。
    merged: dict[str, dict] = {}
    for row in internal_rows:
        key = row.day.strftime('%Y-%m-%d')
        merged[key] = {
            'granted': Decimal(str(row.granted or 0)),
            'consumed': Decimal('0'),
            'count': int(row.cnt or 0),
            'request_count': 0,
            'token_count': 0,
        }
    for day, consume in consumed_by_day.items():
        key = day.strftime('%Y-%m-%d')
        entry = merged.setdefault(
            key, {'granted': Decimal('0'), 'consumed': Decimal('0'), 'count': 0, 'request_count': 0, 'token_count': 0}
        )
        entry['consumed'] = consume['consumed_credits']  # 已为 ≤0
        entry['request_count'] = consume['request_count']
        entry['token_count'] = consume['token_count']
        entry['count'] += consume['request_count']

    # 4) 按日倒序 + 在合并结果上分页。
    all_days = sorted(merged.keys(), reverse=True)
    total = len(all_days)
    page_days = all_days[(page - 1) * size: (page - 1) * size + size]
    items = [
        CreditDailyItem(
            date=key,
            consumed=merged[key]['consumed'],
            granted=merged[key]['granted'],
            net=merged[key]['granted'] + merged[key]['consumed'],
            count=merged[key]['count'],
            request_count=merged[key]['request_count'],
            token_count=merged[key]['token_count'],
        )
        for key in page_days
    ]
    total_pages = (total + size - 1) // size if size else 0
    return response_base.success(
        data=CreditDailyPage(items=items, total=total, page=page, size=size, total_pages=total_pages)
    )


def _calculate_remaining_value(
    current_price: Decimal,
    subscription_end_date: datetime | None,
    subscription_type: str,
) -> tuple[Decimal, int]:
    """计算当前订阅的剩余价值"""
    if not subscription_end_date or current_price <= 0:
        return Decimal('0'), 0

    now = timezone.now()
    if now >= subscription_end_date:
        return Decimal('0'), 0

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

    target_tier = await subscription_tier_dao.select_model_by_column(db, tier_name=body.tier_name, enabled=True, app_code=app_code)
    if not target_tier:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message=f'订阅等级 {body.tier_name} 不存在或未启用',
            target_tier=body.tier_name,
            target_tier_display=body.tier_name,
            subscription_type=body.subscription_type,
            original_price=Decimal('0'),
            remaining_value=Decimal('0'),
            final_price=Decimal('0'),
            remaining_days=0,
            current_tier='',
            current_subscription_type='',
        ))

    subscription = await credit_service.get_or_create_subscription(db, user_id, app_code)
    current_subscription_type = getattr(subscription, 'subscription_type', 'monthly') or 'monthly'

    current_tier_config = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier, app_code=app_code)
    current_price = Decimal('0')
    if current_tier_config:
        if current_subscription_type == 'yearly' and current_tier_config.yearly_price:
            current_price = current_tier_config.yearly_price
        else:
            current_price = current_tier_config.monthly_price or Decimal('0')

    if subscription.tier == body.tier_name and current_subscription_type == body.subscription_type:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message=f'您已经是 {target_tier.display_name} {"年度" if body.subscription_type == "yearly" else "月度"}用户',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal('0'),
            remaining_value=Decimal('0'),
            final_price=Decimal('0'),
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
            original_price=Decimal('0'),
            remaining_value=Decimal('0'),
            final_price=Decimal('0'),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    target_price_monthly = target_tier.monthly_price or Decimal('0')
    current_price_monthly = current_tier_config.monthly_price if current_tier_config else Decimal('0')
    if target_price_monthly < current_price_monthly:
        return response_base.success(data=UpgradePriceResult(
            can_upgrade=False,
            message='不支持降级订阅',
            target_tier=body.tier_name,
            target_tier_display=target_tier.display_name,
            subscription_type=body.subscription_type,
            original_price=Decimal('0'),
            remaining_value=Decimal('0'),
            final_price=Decimal('0'),
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
            original_price=Decimal('0'),
            remaining_value=Decimal('0'),
            final_price=Decimal('0'),
            remaining_days=0,
            current_tier=subscription.tier,
            current_subscription_type=current_subscription_type,
        ))

    if body.subscription_type == 'yearly':
        original_price = target_tier.yearly_price
    else:
        original_price = target_tier.monthly_price

    subscription_end = getattr(subscription, 'subscription_end_date', None)
    remaining_value, remaining_days = _calculate_remaining_value(
        current_price,
        subscription_end,
        current_subscription_type,
    )

    final_price = original_price - remaining_value
    if final_price < 0:
        final_price = Decimal('0')

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
    summary='升级订阅（模拟支付）',
    description='升级到更高级的订阅等级，使用模拟支付，按比例折算剩余价值',
    dependencies=[DependsJwtAuth],
)
async def upgrade_subscription(
    request: Request,
    db: CurrentSessionTransaction,
    body: UpgradeSubscriptionRequest,
) -> ResponseSchemaModel[PaymentResult]:
    """升级订阅"""
    user_id = request.user.id
    app_code = request.state.app_code

    target_tier = await subscription_tier_dao.select_model_by_column(db, tier_name=body.tier_name, enabled=True, app_code=app_code)
    if not target_tier:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message=f'订阅等级 {body.tier_name} 不存在或未启用',
        ))

    subscription = await credit_service.get_or_create_subscription(db, user_id, app_code)
    current_subscription_type = getattr(subscription, 'subscription_type', 'monthly') or 'monthly'

    current_tier_config = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier, app_code=app_code)
    current_price = Decimal('0')
    if current_tier_config:
        if current_subscription_type == 'yearly' and current_tier_config.yearly_price:
            current_price = current_tier_config.yearly_price
        else:
            current_price = current_tier_config.monthly_price or Decimal('0')

    if subscription.tier == body.tier_name and current_subscription_type == body.subscription_type:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message=f'您已经是 {target_tier.display_name} {"年度" if body.subscription_type == "yearly" else "月度"}用户',
        ))

    if body.subscription_type not in ('monthly', 'yearly'):
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message='无效的订阅类型，请选择 monthly 或 yearly',
        ))

    if current_subscription_type == 'yearly' and body.subscription_type == 'monthly':
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message='年度订阅用户不能切换为月度订阅',
        ))

    target_price_monthly = target_tier.monthly_price or Decimal('0')
    current_price_monthly = current_tier_config.monthly_price if current_tier_config else Decimal('0')
    if target_price_monthly < current_price_monthly:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message='不支持降级订阅',
        ))

    if body.subscription_type == 'yearly' and not target_tier.yearly_price:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message=f'{target_tier.display_name} 暂不支持年度订阅',
        ))

    if body.subscription_type == 'yearly':
        original_price = target_tier.yearly_price
    else:
        original_price = target_tier.monthly_price

    subscription_end = getattr(subscription, 'subscription_end_date', None)
    remaining_value, remaining_days = _calculate_remaining_value(
        current_price,
        subscription_end,
        current_subscription_type,
    )

    final_price = original_price - remaining_value
    if final_price < 0:
        final_price = Decimal('0')

    order_id = f'SUB-{uuid.uuid4().hex[:12].upper()}'
    subscription_type_display = '年度' if body.subscription_type == 'yearly' else '月度'
    log.info(f'[Subscription] 模拟支付: user_id={user_id}, tier={body.tier_name}, type={body.subscription_type}, '
             f'order_id={order_id}, original_price={original_price}, remaining_value={remaining_value}, final_price={final_price}')

    balance_before = await credit_service.get_total_available_credits(db, user_id, app_code)

    now = timezone.now()
    if body.subscription_type == 'yearly':
        subscription_end_new = now + timedelta(days=365)
        cycle_end = now + timedelta(days=30)
        next_grant = now + timedelta(days=30)
    else:
        subscription_end_new = now + timedelta(days=30)
        cycle_end = now + timedelta(days=30)
        next_grant = None

    old_tier = subscription.tier
    subscription.tier = body.tier_name
    subscription.subscription_type = body.subscription_type
    subscription.monthly_credits = target_tier.monthly_credits
    subscription.max_agents = target_tier.max_agents
    subscription.billing_cycle_start = now
    subscription.billing_cycle_end = cycle_end
    subscription.subscription_start_date = now
    subscription.subscription_end_date = subscription_end_new
    subscription.next_grant_date = next_grant
    subscription.status = 'active'

    from backend.app.billing.model import CreditTransaction, UserCreditBalance
    upgrade_balance = UserCreditBalance(
        app_code=app_code,
        user_id=user_id,
        credit_type='monthly',
        original_amount=target_tier.monthly_credits,
        used_amount=Decimal('0'),
        remaining_amount=target_tier.monthly_credits,
        expires_at=cycle_end,
        granted_at=now,
        source_type='subscription_upgrade',
        source_reference_id=order_id,
        description=f'{subscription_type_display}订阅: {body.tier_name} (第1个月)',
    )
    db.add(upgrade_balance)

    new_total = balance_before + target_tier.monthly_credits
    subscription.current_credits = new_total

    transaction = CreditTransaction(
        app_code=app_code,
        user_id=user_id,
        transaction_type='subscription_upgrade',
        credits=target_tier.monthly_credits,
        balance_before=balance_before,
        balance_after=new_total,
        reference_id=order_id,
        reference_type='payment',
        description=f'{subscription_type_display}订阅: {old_tier} -> {body.tier_name}',
        extra_data={
            'old_tier': old_tier,
            'new_tier': body.tier_name,
            'subscription_type': body.subscription_type,
            'original_price': float(original_price),
            'remaining_value': float(remaining_value),
            'final_price': float(final_price),
        },
    )
    db.add(transaction)

    log.info(f'[Subscription] 订阅升级成功: user_id={user_id}, {old_tier} -> {body.tier_name} ({body.subscription_type})')

    price_info = f'（原价¥{original_price}'
    if remaining_value > 0:
        price_info += f'，折抵¥{remaining_value}'
    price_info += f'，实付¥{final_price}）'

    return response_base.success(data=PaymentResult(
        success=True,
        order_id=order_id,
        message=f'成功订阅 {target_tier.display_name} {subscription_type_display}版{price_info}',
        new_credits=subscription.current_credits,
        new_tier=body.tier_name,
    ))


@router.post(
    '/purchase',
    summary='购买积分包（模拟支付）',
    description='购买积分包，使用模拟支付',
    dependencies=[DependsJwtAuth],
)
async def purchase_credits(
    request: Request,
    db: CurrentSessionTransaction,
    body: PurchaseCreditsRequest,
) -> ResponseSchemaModel[PaymentResult]:
    """购买积分包"""
    user_id = request.user.id
    app_code = request.state.app_code

    package = await credit_package_dao.select_model(db, pk=body.package_id)
    if not package or not package.enabled:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message='积分包不存在或未启用',
        ))

    total_credits = package.credits + package.bonus_credits

    order_id = f'CRD-{uuid.uuid4().hex[:12].upper()}'
    log.info(f'[Subscription] 模拟支付: user_id={user_id}, package={package.package_name}, order_id={order_id}, price={package.price}')

    subscription = await credit_service.add_credits(
        db,
        user_id=user_id,
        credits=total_credits,
        transaction_type='purchase',
        reference_id=order_id,
        reference_type='payment',
        description=f'购买积分包: {package.package_name} ({package.credits}+{package.bonus_credits})',
        is_purchased=True,
        app_code=app_code,
    )

    log.info(f'[Subscription] 积分购买成功: user_id={user_id}, credits={total_credits}, balance={subscription.current_credits}')

    return response_base.success(data=PaymentResult(
        success=True,
        order_id=order_id,
        message=f'成功购买 {package.package_name}，获得 {total_credits} 积分',
        new_credits=subscription.current_credits,
    ))
