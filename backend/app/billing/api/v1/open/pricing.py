"""订阅定价公开 API - 无需认证

路径前缀: /api/v1/user_tier/open
用于: 官网定价页展示套餐和积分包列表 + 通用加购区报价

三个端点分工：
- `/tiers`、`/packages`：**形状特化**的历史端点（订阅档、积分包各有各的响应模型）；
- `/offerings`：**通用报价面**（G4 · 实施/95），按 `kind` 返回 offering + 其全部档位，
  新增加购/买断型商品只加库行即可被前端渲染，不必再造第三个特化端点。

**数据源（doc94 D1）**：套餐与积分包一律取自商品目录 `billing_offering` / `billing_plan`，
不再读 `subscription_tier` / `credit_package`——这两张表随 D1 删除。展示字段（display_name、
features、description）在 D1 迁移里已搬进 `billing_plan.display_json`；价格取 `price_amount`；
配额取 `quota_json`。**本端点必须先于 drop 迁移上线**，否则定价页与 daemon 的两个已发布
端点会同时断掉。

@author Ysf
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from backend.common.exception import errors
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


class OfferingPlanItem(BaseModel):
    """通用商品的一个档位（价格 + 周期 + 配额 + 试用，原样透出目录快照）。"""

    plan_key: str = Field(description='档位键（下单时原样回传给 plan_key）')
    price_amount: Decimal | None = Field(None, description='价格（price_unit 单位）；未配置价则为空')
    price_unit: str = Field(description='计价单位 cny=人民币元 / credits=积分')
    cycle: str = Field(description='计费周期 once=一次买断 / month=月 / year=年')
    quota_json: dict = Field(description='配额包快照（购买后按份数累加进权益行）')
    trial_json: dict = Field(description='试用策略（enabled/days/times）')
    display_json: dict = Field(description='展示字段快照（display_name/features/description 等）')
    sort_order: int = Field(description='排序权重（升序）')


class OfferingQuoteItem(BaseModel):
    """一个可售商品的完整报价（offering 行 + 它名下的档位 + 购买深链）。"""

    offering_key: str = Field(description='商品业务键（下单时原样回传给 offering_key）')
    feature_key: str = Field(description='付费墙特征键（与权益行、resolve_access 同一把钥匙）')
    kind: str = Field(description='商品种类 llm_tier/credit_pack/app/seat/feature_plan/lead_pack')
    display_name: str = Field(description='商品显示名')
    status: str = Field(description='上架状态 active/inactive')
    sort_order: int = Field(description='排序权重（升序）')
    plans: list[OfferingPlanItem] = Field(description='该商品名下的档位（按 sort_order 升序）')
    purchase_uri: str = Field(description='客户端无关购买深链 hasn://billing/offering/{key}')


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


@router.get(
    '/offerings',
    summary='获取通用商品报价（公开·按 kind 必填过滤）',
    description=(
        '按商品种类（kind）返回商品及其全部档位（价格/周期/配额/试用/购买深链），'
        '用于渲染通用加购区。**kind 为必填**：商品目录没有产品线维度，不收窄就会把多条产品线的'
        '商品混在一起返回。'
    ),
    name='open_get_offering_quotes',
)
async def get_offering_quotes(
    db: CurrentSession,
    kind: Annotated[
        str,
        Query(description='商品种类（必填）feature_plan/llm_tier/credit_pack/app/seat/lead_pack'),
    ],
    status: Annotated[
        str,
        Query(description="上架状态过滤，active（默认）/ inactive / all（不过滤）"),
    ] = 'active',
    offering_key: Annotated[
        str | None,
        Query(description='进一步收窄到单个商品业务键（推荐：调用方明确知道自己要卖哪个商品时用）'),
    ] = None,
) -> ResponseSchemaModel[list[OfferingQuoteItem]]:
    """通用商品报价端点（G4 · 实施/95）——新增加购商品**只加库行，前端不改**。

    **`kind` 必填是刻意的接口层护栏**：`billing_offering` 没有 `app_code` 列（doc05 §3 记录了
    此约束），全表混着多条产品线的商品。若允许不带过滤全量返回，前端一旦直接渲染就会重演桌面端
    串档事故（知小鸦档位与唤星档位在同一网格里并排显示）。收窄到 kind 之后，调用方仍应按
    `offering_key` 进一步收窄——**不得把返回值整体铺开渲染**。

    出参每项自带 `purchase_uri`，与付费墙拒绝时透出的 `AccessDecision.offer.purchase_uri` 同源，
    「撞墙引导」与「主动加购」点进同一个购买流程。
    """
    from backend.app.billing.core.fulfillment import KNOWN_KINDS
    from backend.app.billing.service import offering_pricing

    if kind not in KNOWN_KINDS:
        # 拼错一个字母就回空列表，会让前端把整块加购区渲染成「暂无商品」而无人察觉——明确 4xx。
        raise errors.RequestError(msg=f'未知的商品种类: {kind}（合法取值 {sorted(KNOWN_KINDS)}）')
    if status not in ('active', 'inactive', 'all'):
        raise errors.RequestError(msg=f'未知的状态过滤: {status}（合法取值 active/inactive/all）')

    quotes = await offering_pricing.list_offering_quotes(
        db,
        kind=kind,
        status=None if status == 'all' else status,
        offering_key=offering_key,
    )
    items = [
        OfferingQuoteItem(
            offering_key=quote.offering.key,
            feature_key=quote.offering.feature_key,
            kind=quote.offering.kind,
            display_name=quote.offering.display_name,
            status=quote.offering.status,
            sort_order=quote.offering.sort_order,
            plans=[
                OfferingPlanItem(
                    plan_key=plan.plan_key,
                    price_amount=plan.price_amount,
                    price_unit=plan.price_unit,
                    cycle=plan.cycle,
                    quota_json=plan.quota_json or {},
                    trial_json=plan.trial_json or {},
                    display_json=plan.display_json or {},
                    sort_order=plan.sort_order,
                )
                for plan in quote.plans
            ],
            purchase_uri=offering_pricing.purchase_uri(quote.offering.key),
        )
        for quote in quotes
    ]
    return response_base.success(data=items)
