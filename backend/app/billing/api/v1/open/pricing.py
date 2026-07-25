"""订阅定价公开 API - 无需认证

路径前缀: /api/v1/user_tier/open
用于: 官网定价页展示套餐和积分包列表

**数据源（doc94 D1）**：套餐与积分包一律取自商品目录 `billing_offering` / `billing_plan`，
不再读 `subscription_tier` / `credit_package`——这两张表随 D1 删除。展示字段（display_name、
features、description）在 D1 迁移里已搬进 `billing_plan.display_json`；价格取 `price_amount`；
配额取 `quota_json`。**本端点必须先于 drop 迁移上线**，否则定价页与 daemon 的两个已发布
端点会同时断掉。

@author Ysf
"""

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


def _decimal_of(payload: dict, key: str) -> Decimal:
    """从 JSONB 快照里取数值；缺失或不可解析时按 0 处理并保持类型稳定。

    这里允许回落 0：这些是展示用的额度数字，缺档时页面显示 0 比抛 500 更可用；
    余额类数字从不走这条路——那一侧的规则是「读不到就说读不到」。
    """
    raw = payload.get(key)
    if raw is None:
        return Decimal(0)
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return Decimal(0)


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
    """获取订阅等级列表（doc94 D1：唯一数据源是商品目录 plan，不再读 subscription_tier）。

    一个订阅档在目录里是两条 plan：月付 `<tier>`、年付 `<tier>_yearly`。这里按档聚合回
    一行，年付缺档时 `yearly_price` 为空——不编造年价。
    """
    from backend.app.billing.service import offering_pricing

    del request  # app_code 维度由商品目录统一承载，这里不再按 app_code 过滤
    items = [
        SubscriptionTierItem(
            id=tier.plan_id,
            tier_name=tier.tier_name,
            display_name=tier.display_name,
            monthly_credits=tier.credits_per_cycle,
            monthly_price=tier.monthly_price or Decimal(0),
            yearly_price=tier.yearly_price,
            yearly_discount=tier.yearly_discount,
            features=tier.features,
        )
        for tier in await offering_pricing.list_tiers(db)
    ]

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
    """获取积分包列表（doc94 D1：唯一数据源是商品目录 plan，不再读 credit_package）。"""
    from backend.app.billing.service import offering_pricing

    del request  # app_code 维度由商品目录统一承载，这里不再按 app_code 过滤
    items = [
        CreditPackageItem(
            id=pack.plan_id,
            package_name=pack.package_name,
            credits=pack.credits,
            price=pack.price or Decimal(0),
            bonus_credits=pack.bonus_credits,
            description=pack.description,
        )
        for pack in await offering_pricing.list_credit_packs(db)
    ]

    return response_base.success(data=items)
