"""订阅定价公开 API - 无需认证

路径前缀: /api/v1/user_tier/open
用于: 官网定价页展示套餐和积分包列表

@author Ysf
"""

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


# ==================== Response Schemas ====================


class SubscriptionTierItem(BaseModel):
    """订阅等级项"""

    id: int
    tier_name: str
    display_name: str
    monthly_credits: Decimal
    monthly_price: Decimal
    yearly_price: Decimal | None = None
    yearly_discount: Decimal | None = None
    features: dict | None = None


class CreditPackageItem(BaseModel):
    """积分包项"""

    id: int
    package_name: str
    credits: Decimal
    price: Decimal
    bonus_credits: Decimal
    description: str | None = None


# ==================== APIs ====================


@router.get(
    '/tiers',
    summary='获取订阅等级列表（公开）',
    description='获取所有可用的订阅等级，无需登录',
    name='open_get_subscription_tiers',
)
async def get_subscription_tiers(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[list[SubscriptionTierItem]]:
    """获取订阅等级列表（MK-5：价格以商品目录 plan 为权威，display/features 仍从 legacy 表）"""
    from sqlalchemy import select

    from backend.app.billing.model import SubscriptionTier
    from backend.app.billing.service import offering_pricing

    app_code = request.state.app_code
    stmt = (
        select(SubscriptionTier)
        .where(SubscriptionTier.enabled, SubscriptionTier.app_code == app_code)
        .order_by(SubscriptionTier.sort_order)
    )
    result = await db.execute(stmt)
    tiers = result.scalars().all()

    # MK-5：叠加商品目录 plan 权威价（改价即时生效于展示与新单）；plan 缺档回落 legacy 价。
    plans = await offering_pricing.active_plans_map(db, offering_pricing.OFFERING_LLM_TIER)
    items = []
    for t in tiers:
        monthly_key, yearly_key = offering_pricing.tier_plan_keys(t.tier_name)
        monthly_plan = plans.get(monthly_key)
        yearly_plan = plans.get(yearly_key)
        items.append(
            SubscriptionTierItem(
                id=t.id,
                tier_name=t.tier_name,
                display_name=t.display_name,
                monthly_credits=t.monthly_credits,
                monthly_price=monthly_plan.price_amount if monthly_plan else t.monthly_price,
                yearly_price=yearly_plan.price_amount if yearly_plan else t.yearly_price,
                yearly_discount=t.yearly_discount,
                features=t.features,
            )
        )

    return response_base.success(data=items)


@router.get(
    '/packages',
    summary='获取积分包列表（公开）',
    description='获取所有可购买的积分包，无需登录',
    name='open_get_credit_packages',
)
async def get_credit_packages(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[list[CreditPackageItem]]:
    """获取积分包列表（MK-5：价格以商品目录 plan 为权威，display 仍从 legacy 表）"""
    from sqlalchemy import select

    from backend.app.billing.model import CreditPackage
    from backend.app.billing.service import offering_pricing

    app_code = request.state.app_code
    stmt = (
        select(CreditPackage)
        .where(CreditPackage.enabled, CreditPackage.app_code == app_code)
        .order_by(CreditPackage.sort_order)
    )
    result = await db.execute(stmt)
    packages = result.scalars().all()

    # MK-5：叠加商品目录 plan 权威价（积分包 plan_key = package_name）；plan 缺档回落 legacy 价。
    plans = await offering_pricing.active_plans_map(db, offering_pricing.OFFERING_CREDITS_TOPUP)
    items = []
    for p in packages:
        plan = plans.get(p.package_name)
        items.append(
            CreditPackageItem(
                id=p.id,
                package_name=p.package_name,
                credits=p.credits,
                price=plan.price_amount if plan else p.price,
                bonus_credits=p.bonus_credits,
                description=p.description,
            )
        )

    return response_base.success(data=items)
