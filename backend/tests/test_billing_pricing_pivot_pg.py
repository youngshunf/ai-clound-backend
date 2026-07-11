"""MK-5 定价读侧收编·真实 PG（实施/92 MK-5）——零 mock。

覆盖「LLM 订阅/积分定价切读 offering·出参字段名不变·改价只影响新单」：
1. 出参 schema 字段名锁死（SubscriptionTierItem/CreditPackageItem 回归锚·风险#2）；
2. plan 为价格权威：active_plans_map / plan_price 取 billing_plan 权威价；
3. 列表叠加逻辑：plan 命中用 plan 价、缺档回落 legacy 价（出参字段名不变）；
4. 改价即时：改 billing_plan.price_amount → plan_price 反映新价（下单/列表都以其为准）。

需本地 PostgreSQL :15432（含 hasn_billing.billing_offering/billing_plan/subscription_tier/credit_package）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.api.v1.open.pricing import CreditPackageItem, SubscriptionTierItem
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.credit_package import CreditPackage
from backend.app.billing.model.subscription_tier import SubscriptionTier
from backend.app.billing.service import offering_pricing
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = 'mk5_pricing_test'
_TIER = 'mk5pro'


# ── 1. 出参 schema 字段名锁死（回归锚·前端零硬编码本就无感）──
def test_subscription_tier_item_fields_locked() -> None:
    assert set(SubscriptionTierItem.model_fields) == {
        'id',
        'tier_name',
        'display_name',
        'monthly_credits',
        'monthly_price',
        'yearly_price',
        'yearly_discount',
        'features',
    }


def test_credit_package_item_fields_locked() -> None:
    assert set(CreditPackageItem.model_fields) == {
        'id',
        'package_name',
        'credits',
        'price',
        'bonus_credits',
        'description',
    }


def test_tier_plan_keys() -> None:
    assert offering_pricing.tier_plan_keys('pro') == ('pro', 'pro_yearly')


# ── 2/3/4. 真实 PG：plan 为价格权威 + 缺档回落 + 改价即时 ──
@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()

    async def _purge() -> None:
        await s.execute(
            text('DELETE FROM hasn_billing.billing_plan WHERE offering_key IN (:a, :b)'),
            {'a': offering_pricing.OFFERING_LLM_TIER, 'b': offering_pricing.OFFERING_CREDITS_TOPUP},
        )
        await s.execute(text('DELETE FROM hasn_billing.subscription_tier WHERE app_code = :a'), {'a': _APP})
        await s.execute(text('DELETE FROM hasn_billing.credit_package WHERE app_code = :a'), {'a': _APP})
        await s.commit()

    try:
        await _purge()
        yield s
    finally:
        await _purge()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def _ensure_offering(s, key: str, kind: str) -> None:
    exists = (await s.execute(select(BillingOffering.id).where(BillingOffering.key == key))).scalars().first()
    if exists is None:
        s.add(BillingOffering(key=key, kind=kind, feature_key=key, display_name=key, status='active'))
        await s.flush()


async def test_plan_is_price_authority_for_tiers(sess) -> None:
    """月付/年付价取 billing_plan 权威值；plan 缺档的字段回落 legacy 价。"""
    await _ensure_offering(sess, offering_pricing.OFFERING_LLM_TIER, 'llm_tier')
    # legacy tier：月 99 / 年 999（作回落基准）
    sess.add(
        SubscriptionTier(
            app_code=_APP,
            tier_name=_TIER,
            display_name='MK5 专业版',
            monthly_credits=Decimal(1000),
            monthly_price=Decimal('99.00'),
            yearly_price=Decimal('999.00'),
            max_agents=3,
            enabled=True,
            sort_order=1,
        )
    )
    # plan：仅月付档存在，权威价 88（≠ legacy 99）；年付档不建 → 回落 legacy 999
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_LLM_TIER,
            plan_key=_TIER,
            price_amount=Decimal('88.00'),
            price_unit='cny',
            cycle='month',
            status='active',
        )
    )
    await sess.commit()

    monthly_key, yearly_key = offering_pricing.tier_plan_keys(_TIER)
    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_LLM_TIER, monthly_key) == Decimal('88.00')
    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_LLM_TIER, yearly_key) is None

    plans = await offering_pricing.active_plans_map(sess, offering_pricing.OFFERING_LLM_TIER)
    # 叠加契约（= 端点内联逻辑）：命中 plan 用 plan 价、缺档回落 legacy
    tier = (await sess.execute(select(SubscriptionTier).where(SubscriptionTier.tier_name == _TIER))).scalar_one()
    monthly = plans[monthly_key].price_amount if monthly_key in plans else tier.monthly_price
    yearly = plans[yearly_key].price_amount if yearly_key in plans else tier.yearly_price
    assert monthly == Decimal('88.00'), '月付应取 plan 权威价'
    assert yearly == Decimal('999.00'), '年付无 plan 档应回落 legacy 价'


async def test_repricing_reflects_immediately(sess) -> None:
    """改 billing_plan.price_amount → plan_price 立即反映新价（改价只影响新单）。"""
    await _ensure_offering(sess, offering_pricing.OFFERING_LLM_TIER, 'llm_tier')
    plan = BillingPlan(
        offering_key=offering_pricing.OFFERING_LLM_TIER,
        plan_key=_TIER,
        price_amount=Decimal('88.00'),
        price_unit='cny',
        cycle='month',
        status='active',
    )
    sess.add(plan)
    await sess.commit()
    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_LLM_TIER, _TIER) == Decimal('88.00')

    plan.price_amount = Decimal('66.00')  # admin 商业化中心改价
    await sess.commit()
    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_LLM_TIER, _TIER) == Decimal('66.00')


async def test_plan_is_price_authority_for_packages(sess) -> None:
    """积分包价取 billing_plan（plan_key = package_name）；缺档回落 legacy。"""
    await _ensure_offering(sess, offering_pricing.OFFERING_CREDITS_TOPUP, 'credit_pack')
    sess.add(
        CreditPackage(
            app_code=_APP,
            package_name='mk5pack',
            credits=Decimal(500),
            price=Decimal('50.00'),
            bonus_credits=Decimal(50),
            enabled=True,
            sort_order=1,
        )
    )
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_CREDITS_TOPUP,
            plan_key='mk5pack',
            price_amount=Decimal('45.00'),
            price_unit='cny',
            cycle='once',
            status='active',
        )
    )
    await sess.commit()

    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_CREDITS_TOPUP, 'mk5pack') == Decimal(
        '45.00'
    )
    # 缺档回落
    assert await offering_pricing.plan_price(sess, offering_pricing.OFFERING_CREDITS_TOPUP, 'nonexistent') is None
