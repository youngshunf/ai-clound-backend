"""商品目录定价读侧收编（内核唯一价格权威 · doc02 §3.4/§5 · 实施/92 MK-5）。

MK-5「LLM 订阅/积分定价收编」：价格权威从 subscription_tier/credit_package 迁到
billing_plan——**改价只影响新购续费**（admin 商业化中心改 plan.price_amount 即时生效于
新单，已购权益的配额/周期由购买时固化的快照兜住，不被穿透）。

读侧两处消费：
- 定价列表端点（tiers/packages，桌面端+官网）：显示价格叠加 plan 权威价（出参字段名不变）；
- 下单入口（pay_order_service）：pay_amount 以 plan 为准。

legacy 表（subscription_tier/credit_package）降级为「只读遗留」——仍供 display_name/features
等静态展示字段，但价格不再从它读；plan 缺失时（存量种子未覆盖）回落 legacy 价，绝不因缺档报错。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §3.4/§5。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.billing.model.billing_plan import BillingPlan

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# LLM 订阅 offering 业务键（与 seed 对齐）
OFFERING_LLM_TIER = 'llm:tier'
# 积分充值 offering 业务键（与 seed 对齐）
OFFERING_CREDITS_TOPUP = 'credits:topup'


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
