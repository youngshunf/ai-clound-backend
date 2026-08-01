"""商品目录定价读侧收编（内核唯一价格权威 · doc02 §3.4/§5 · 实施/92 MK-5）。

MK-5「LLM 订阅/积分定价收编」：价格权威从 subscription_tier/credit_package 迁到
billing_plan——**改价只影响新购续费**（admin 商业化中心改 plan.price_amount 即时生效于
新单，已购权益的配额/周期由购买时固化的快照兜住，不被穿透）。

读侧两处消费：
- 定价列表端点（tiers/packages，桌面端+官网）：显示价格叠加 plan 权威价（出参字段名不变）；
- 下单入口（pay_order_service）：pay_amount 以 plan 为准。

**doc94 D1 起，legacy 表（subscription_tier/credit_package）不再是任何读路径的数据源**：
展示字段已迁进 `billing_plan.display_json`、每周期额度进 `quota_json.credits_per_cycle`，
两张表随 D1 drop。凡是过去从 legacy 表取「档位配置」的地方，一律改走本模块的
`get_tier` / `list_tiers` / `get_credit_pack`——**目录是唯一档位事实源**。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §3.4/§5。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# LLM 订阅 offering 业务键（与 seed 对齐）
OFFERING_LLM_TIER = 'llm:tier'
# 积分充值 offering 业务键（与 seed 对齐）
OFFERING_CREDITS_TOPUP = 'credits:topup'

#: 目录行的上架状态取值（`billing_offering.status` / `billing_plan.status` 共用）。
STATUS_ACTIVE = 'active'


def purchase_uri(offering_key: str) -> str:
    """商品的客户端无关购买深链。

    与 `access_service._build_offer` 透出的 `AccessDecision.offer.purchase_uri` **必须是同一形态**——
    「撞墙后引导购买」与「主动加购」点的是同一个商品，两处漂移就会出现同一商品两条深链。
    """
    return f'hasn://billing/offering/{offering_key}'


async def active_plans_map(db: AsyncSession, offering_key: str) -> dict[str, BillingPlan]:
    """取某 offering 下全部 active plan，按 plan_key 索引（列表端点批量叠加价，避免 N+1）。"""
    rows = (
        (
            await db.execute(
                sa.select(BillingPlan).where(
                    BillingPlan.offering_key == offering_key,
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .all()
    )
    return {p.plan_key: p for p in rows}


async def plan_price(db: AsyncSession, offering_key: str, plan_key: str) -> Decimal | None:
    """取某 (offering_key, plan_key) 的 active plan 权威价；无 active 档返回 None（调用方回落 legacy）。"""
    price = (
        (
            await db.execute(
                sa.select(BillingPlan.price_amount).where(
                    BillingPlan.offering_key == offering_key,
                    BillingPlan.plan_key == plan_key,
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .first()
    )
    return price


def tier_plan_keys(tier_name: str) -> tuple[str, str]:
    """LLM 订阅档的 (月付 plan_key, 年付 plan_key)——与 seed 对齐（年付档 <tier>_yearly）。"""
    return tier_name, f'{tier_name}_yearly'


# ── doc94 D1：档位事实源从 legacy 表迁到商品目录 ──


def _decimal_of(payload: dict[str, Any], key: str, fallback: Decimal = Decimal(0)) -> Decimal:
    """从 JSONB 快照取数值；缺失或不可解析回落 fallback。

    这些是**目录配置**（每周期额度、包内积分），不是用户余额。目录缺档回落 0 只影响
    展示与下单校验，会被明确的「档位不存在」挡住；余额一侧永远不走这条路。
    """
    raw = payload.get(key)
    if raw is None:
        return fallback
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return fallback


def _non_negative_int_of(payload: dict[str, Any], key: str) -> int:
    """从目录 JSONB 读取非负整数字节数，非法配置按未配置处理。"""
    raw = payload.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


@dataclass(slots=True, frozen=True)
class TierCatalogEntry:
    """一个 LLM 订阅档在目录里的完整形态（月付 plan 为主档，年付 plan 只补价）。"""

    tier_name: str
    display_name: str
    credits_per_cycle: Decimal
    monthly_price: Decimal | None
    yearly_price: Decimal | None
    yearly_discount: Decimal | None
    max_agents: int
    storage_bytes: int
    sort_order: int
    features: dict[str, Any] | None
    plan_id: int


@dataclass(slots=True, frozen=True)
class CreditPackCatalogEntry:
    """一个积分包在目录里的完整形态。"""

    package_name: str
    credits: Decimal
    bonus_credits: Decimal
    price: Decimal | None
    description: str | None
    sort_order: int
    plan_id: int


def _build_tier(tier_name: str, monthly: BillingPlan, yearly: BillingPlan | None) -> TierCatalogEntry:
    display = monthly.display_json or {}
    quota = monthly.quota_json or {}
    return TierCatalogEntry(
        tier_name=tier_name,
        display_name=str(display.get('display_name') or tier_name),
        credits_per_cycle=_decimal_of(quota, 'credits_per_cycle'),
        monthly_price=monthly.price_amount,
        yearly_price=yearly.price_amount if yearly else None,
        yearly_discount=_decimal_of(display, 'yearly_discount', Decimal(0)) or None,
        max_agents=int(quota.get('max_agents') or 0),
        storage_bytes=_non_negative_int_of(quota, 'storage_bytes'),
        sort_order=monthly.sort_order,
        features=display.get('features') if isinstance(display.get('features'), dict) else None,
        plan_id=monthly.id,
    )


async def list_tiers(db: AsyncSession) -> list[TierCatalogEntry]:
    """列出全部 active 订阅档（按 sort_order）。

    只有月付 plan 的档才成立：仅存年付 plan 时跳过，而不是拿年价冒充月价。
    """
    plans = await active_plans_map(db, OFFERING_LLM_TIER)
    monthlies = {key: plan for key, plan in plans.items() if not key.endswith('_yearly')}
    entries = [_build_tier(key, plan, plans.get(f'{key}_yearly')) for key, plan in monthlies.items()]
    return sorted(entries, key=lambda e: (e.sort_order, e.tier_name))


async def get_tier(db: AsyncSession, tier_name: str) -> TierCatalogEntry | None:
    """取单个订阅档配置；档位不存在或已下架返回 None（调用方必须显式报错，不许兜底放行）。"""
    monthly_key, yearly_key = tier_plan_keys(tier_name)
    rows = (
        (
            await db.execute(
                sa.select(BillingPlan).where(
                    BillingPlan.offering_key == OFFERING_LLM_TIER,
                    BillingPlan.plan_key.in_([monthly_key, yearly_key]),
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {p.plan_key: p for p in rows}
    monthly = by_key.get(monthly_key)
    if monthly is None:
        return None
    return _build_tier(tier_name, monthly, by_key.get(yearly_key))


def _build_pack(plan: BillingPlan) -> CreditPackCatalogEntry:
    display = plan.display_json or {}
    quota = plan.quota_json or {}
    return CreditPackCatalogEntry(
        package_name=str(display.get('package_name') or plan.plan_key),
        credits=_decimal_of(quota, 'credits'),
        bonus_credits=_decimal_of(quota, 'bonus_credits'),
        price=plan.price_amount,
        description=display.get('description'),
        sort_order=plan.sort_order,
        plan_id=plan.id,
    )


async def list_credit_packs(db: AsyncSession) -> list[CreditPackCatalogEntry]:
    """列出全部 active 积分包（按 sort_order）。"""
    plans = await active_plans_map(db, OFFERING_CREDITS_TOPUP)
    entries = [_build_pack(plan) for plan in plans.values()]
    return sorted(entries, key=lambda e: (e.sort_order, e.package_name))


async def get_credit_pack_by_id(db: AsyncSession, plan_id: int) -> CreditPackCatalogEntry | None:
    """按 plan 主键取积分包（下单入口用）。

    `/packages` 列表回的 `id` 就是 plan 主键，下单请求原样带回来，这里按同一把钥匙查。
    doc94 D1 之前这里查的是 `credit_package.id`，随该表 drop 一并改指。
    """
    plan = (
        (
            await db.execute(
                sa.select(BillingPlan).where(
                    BillingPlan.id == plan_id,
                    BillingPlan.offering_key == OFFERING_CREDITS_TOPUP,
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .first()
    )
    return _build_pack(plan) if plan is not None else None


async def get_credit_pack(db: AsyncSession, package_name: str) -> CreditPackCatalogEntry | None:
    """取单个积分包配置；不存在或已下架返回 None。"""
    plan = (
        (
            await db.execute(
                sa.select(BillingPlan).where(
                    BillingPlan.offering_key == OFFERING_CREDITS_TOPUP,
                    BillingPlan.plan_key == package_name,
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .first()
    )
    if plan is None:
        return None
    return _build_pack(plan)


# ── 通用商品读侧（G2 下单 + G4 报价端点共用 · 实施/95） ──
#
# 上面几个函数都是「某一类商品的专用读法」（订阅档聚合月付/年付、积分包按包名取）。
# 下面这组刻意不认识任何具体商品：只按 `offering_key` / `plan_key` / `kind` 读目录原样返回，
# 让「新增一个商品」不需要在这里再加一个专用读法。


async def get_offering(db: AsyncSession, offering_key: str) -> BillingOffering | None:
    """按业务键取商品行（**不过滤 status**，调用方按场景决定上架校验的严格度）。

    下单入口必须拒下架商品；已付款订单的履约则必须无视下架照发——两种口径都要用这一个读法，
    故此处不替调用方做决定。
    """
    return (
        await db.execute(sa.select(BillingOffering).where(BillingOffering.key == offering_key))
    ).scalars().first()


async def get_active_plan(db: AsyncSession, offering_key: str, plan_key: str) -> BillingPlan | None:
    """按 (offering_key, plan_key) 取 **active** 档位行；不存在或已下架返回 None。"""
    return (
        await db.execute(
            sa.select(BillingPlan).where(
                BillingPlan.offering_key == offering_key,
                BillingPlan.plan_key == plan_key,
                BillingPlan.status == STATUS_ACTIVE,
            )
        )
    ).scalars().first()


@dataclass(slots=True, frozen=True)
class OfferingQuote:
    """一个商品在报价面上的完整形态：offering 行 + 它名下的档位行（按 sort_order）。"""

    offering: BillingOffering
    plans: list[BillingPlan]


async def list_offering_quotes(
    db: AsyncSession,
    *,
    kind: str,
    status: str | None = STATUS_ACTIVE,
    offering_key: str | None = None,
) -> list[OfferingQuote]:
    """列出某 `kind` 下的商品及其档位（报价端点 G4 的唯一取数口径）。

    - `kind` **必填**：`billing_offering` 没有 `app_code` 列（doc05 §3 记录了此约束），
      全表混着多条产品线的商品。强制按 kind 收窄是接口层的最小护栏，调用方还应按
      `offering_key` 进一步收窄，**不得全量渲染**——否则重演桌面端串档（知小鸦档位与唤星档位并排）。
    - `status=None` 表示不过滤上架状态（admin 预览用）；默认只回 `active`。
    - 档位与商品用**同一个 status 口径**：报价面上不该出现「商品上架、档位下架」的半截购买卡。

    一次查两条 SQL（offering 一批、plan 一批）后在内存里归组，避免按商品逐条查 plan 的 N+1。
    """
    off_stmt = sa.select(BillingOffering).where(BillingOffering.kind == kind)
    if status is not None:
        off_stmt = off_stmt.where(BillingOffering.status == status)
    if offering_key:
        off_stmt = off_stmt.where(BillingOffering.key == offering_key)
    off_stmt = off_stmt.order_by(BillingOffering.sort_order, BillingOffering.key)
    offerings = list((await db.execute(off_stmt)).scalars().all())
    if not offerings:
        return []

    keys = [off.key for off in offerings]
    plan_stmt = sa.select(BillingPlan).where(BillingPlan.offering_key.in_(keys))
    if status is not None:
        plan_stmt = plan_stmt.where(BillingPlan.status == status)
    plans = (await db.execute(plan_stmt.order_by(BillingPlan.sort_order, BillingPlan.plan_key))).scalars().all()

    grouped: dict[str, list[BillingPlan]] = {key: [] for key in keys}
    for plan in plans:
        grouped[plan.offering_key].append(plan)
    return [OfferingQuote(offering=off, plans=grouped[off.key]) for off in offerings]
