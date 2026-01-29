"""用户订阅 API - 面向前端用户（JWT 认证）
@author Ysf
"""

from typing import Annotated
from datetime import timedelta
import uuid

from fastapi import APIRouter, Header, Query, Body, Request
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime

from backend.app.user_tier.service.credit_service import credit_service
from backend.app.user_tier.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.user_tier.crud.crud_credit_package import credit_package_dao
from backend.common.response.response_schema import ResponseSchemaModel, ResponseModel, response_base
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


class SubscriptionInfoResponse(BaseModel):
    """订阅信息响应"""
    user_id: int
    tier: str
    tier_display_name: str | None = None
    monthly_credits: Decimal
    current_credits: Decimal
    used_credits: Decimal
    purchased_credits: Decimal
    monthly_remaining: Decimal | None = None
    bonus_remaining: Decimal | None = None
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    status: str
    balances: list[CreditBalanceItem] = []


class SubscriptionTierItem(BaseModel):
    """订阅等级项"""
    id: int
    tier_name: str
    display_name: str
    monthly_credits: Decimal
    monthly_price: Decimal
    features: dict | None = None


class CreditPackageItem(BaseModel):
    """积分包项"""
    id: int
    package_name: str
    credits: Decimal
    price: Decimal
    bonus_credits: Decimal
    description: str | None = None


class UpgradeSubscriptionRequest(BaseModel):
    """升级订阅请求"""
    tier_name: str = Field(description='目标订阅等级')


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
    
    # 获取完整的积分信息（包含 balances）
    info = await credit_service.get_user_credits_info(db, user_id)
    
    # 转换 balances 为 CreditBalanceItem
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
        monthly_credits=Decimal(str(info['monthly_credits'])),
        current_credits=Decimal(str(info['current_credits'])),
        used_credits=Decimal(str(info['used_credits'])),
        purchased_credits=Decimal(str(info['purchased_credits'])),
        monthly_remaining=Decimal(str(info.get('monthly_remaining', 0))),
        bonus_remaining=Decimal(str(info.get('bonus_remaining', 0))),
        billing_cycle_start=datetime.fromisoformat(info['billing_cycle_start']),
        billing_cycle_end=datetime.fromisoformat(info['billing_cycle_end']),
        status=info['status'],
        balances=balances,
    )
    
    return response_base.success(data=data)


@router.get(
    '/tiers',
    summary='获取订阅等级列表',
    description='获取所有可用的订阅等级',
    dependencies=[DependsJwtAuth],
)
async def get_subscription_tiers(
    db: CurrentSession,
) -> ResponseSchemaModel[list[SubscriptionTierItem]]:
    """获取订阅等级列表"""
    from sqlalchemy import select
    from backend.app.user_tier.model import SubscriptionTier
    
    stmt = (
        select(SubscriptionTier)
        .where(SubscriptionTier.enabled == True)
        .order_by(SubscriptionTier.sort_order)
    )
    result = await db.execute(stmt)
    tiers = result.scalars().all()
    
    items = [
        SubscriptionTierItem(
            id=t.id,
            tier_name=t.tier_name,
            display_name=t.display_name,
            monthly_credits=t.monthly_credits,
            monthly_price=t.monthly_price,
            features=t.features,
        )
        for t in tiers
    ]
    
    return response_base.success(data=items)


@router.get(
    '/packages',
    summary='获取积分包列表',
    description='获取所有可购买的积分包',
    dependencies=[DependsJwtAuth],
)
async def get_credit_packages(
    db: CurrentSession,
) -> ResponseSchemaModel[list[CreditPackageItem]]:
    """获取积分包列表"""
    from sqlalchemy import select
    from backend.app.user_tier.model import CreditPackage
    
    stmt = (
        select(CreditPackage)
        .where(CreditPackage.enabled == True)
        .order_by(CreditPackage.sort_order)
    )
    result = await db.execute(stmt)
    packages = result.scalars().all()
    
    items = [
        CreditPackageItem(
            id=p.id,
            package_name=p.package_name,
            credits=p.credits,
            price=p.price,
            bonus_credits=p.bonus_credits,
            description=p.description,
        )
        for p in packages
    ]
    
    return response_base.success(data=items)


@router.post(
    '/upgrade',
    summary='升级订阅（模拟支付）',
    description='升级到更高级的订阅等级，使用模拟支付',
    dependencies=[DependsJwtAuth],
)
async def upgrade_subscription(
    request: Request,
    db: CurrentSessionTransaction,
    body: UpgradeSubscriptionRequest,
) -> ResponseSchemaModel[PaymentResult]:
    """升级订阅"""
    user_id = request.user.id
    
    # 获取目标等级
    target_tier = await subscription_tier_dao.select_model_by_column(db, tier_name=body.tier_name, enabled=True)
    if not target_tier:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message=f'订阅等级 {body.tier_name} 不存在或未启用',
        ))
    
    # 获取用户当前订阅
    subscription = await credit_service.get_or_create_subscription(db, user_id)
    
    # 检查是否已经是该等级
    if subscription.tier == body.tier_name:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message=f'您已经是 {target_tier.display_name} 用户',
        ))
    
    # 模拟支付 - 生成订单号
    order_id = f'SUB-{uuid.uuid4().hex[:12].upper()}'
    log.info(f'[Subscription] 模拟支付: user_id={user_id}, tier={body.tier_name}, order_id={order_id}, price={target_tier.monthly_price}')
    
    # 获取当前总可用积分
    balance_before = await credit_service.get_total_available_credits(db, user_id)
    
    # 更新用户订阅（保留现有积分）
    now = timezone.now()
    cycle_end = now + timedelta(days=30)
    old_tier = subscription.tier
    subscription.tier = body.tier_name
    subscription.monthly_credits = target_tier.monthly_credits
    subscription.billing_cycle_start = now
    subscription.billing_cycle_end = cycle_end
    subscription.status = 'active'
    # 不重置 current_credits 和 used_credits，保留已有积分
    
    # 创建新的积分余额记录（升级发放的积分）
    from backend.app.user_tier.model import CreditTransaction, UserCreditBalance
    upgrade_balance = UserCreditBalance(
        user_id=user_id,
        credit_type='monthly',
        original_amount=target_tier.monthly_credits,
        used_amount=Decimal('0'),
        remaining_amount=target_tier.monthly_credits,
        expires_at=cycle_end,
        granted_at=now,
        source_type='subscription_upgrade',
        source_reference_id=order_id,
        description=f'订阅升级: {old_tier} -> {body.tier_name}',
    )
    db.add(upgrade_balance)
    
    # 更新 subscription 汇总字段（累加新积分）
    new_total = balance_before + target_tier.monthly_credits
    subscription.current_credits = new_total
    
    # 记录交易
    transaction = CreditTransaction(
        user_id=user_id,
        transaction_type='subscription_upgrade',
        credits=target_tier.monthly_credits,
        balance_before=balance_before,
        balance_after=new_total,
        reference_id=order_id,
        reference_type='payment',
        description=f'订阅升级: {old_tier} -> {body.tier_name}',
        extra_data={
            'old_tier': old_tier,
            'new_tier': body.tier_name,
            'price': float(target_tier.monthly_price),
        },
    )
    db.add(transaction)
    
    log.info(f'[Subscription] 订阅升级成功: user_id={user_id}, {old_tier} -> {body.tier_name}')
    
    return response_base.success(data=PaymentResult(
        success=True,
        order_id=order_id,
        message=f'成功升级到 {target_tier.display_name}',
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
    
    # 获取积分包
    package = await credit_package_dao.select_model(db, pk=body.package_id)
    if not package or not package.enabled:
        return response_base.fail(data=PaymentResult(
            success=False,
            order_id='',
            message='积分包不存在或未启用',
        ))
    
    # 计算总积分
    total_credits = package.credits + package.bonus_credits
    
    # 模拟支付 - 生成订单号
    order_id = f'CRD-{uuid.uuid4().hex[:12].upper()}'
    log.info(f'[Subscription] 模拟支付: user_id={user_id}, package={package.package_name}, order_id={order_id}, price={package.price}')
    
    # 增加用户积分
    subscription = await credit_service.add_credits(
        db,
        user_id=user_id,
        credits=total_credits,
        transaction_type='purchase',
        reference_id=order_id,
        reference_type='payment',
        description=f'购买积分包: {package.package_name} ({package.credits}+{package.bonus_credits})',
        is_purchased=True,
    )
    
    log.info(f'[Subscription] 积分购买成功: user_id={user_id}, credits={total_credits}, balance={subscription.current_credits}')
    
    return response_base.success(data=PaymentResult(
        success=True,
        order_id=order_id,
        message=f'成功购买 {package.package_name}，获得 {total_credits} 积分',
        new_credits=subscription.current_credits,
    ))
