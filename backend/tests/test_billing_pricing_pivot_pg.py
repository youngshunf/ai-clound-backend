"""定价读侧收编·真实 PG（实施/92 MK-5 → doc94 D1）——零 mock。

MK-5 时价格权威迁到 `billing_plan`，但展示字段仍回落 legacy 表。**doc94 D1 起
`subscription_tier` / `credit_package` 被删除，商品目录成为档位的唯一事实源**，
本用例随之改锁 D1 契约：

1. 出参 schema 字段名锁死（SubscriptionTierItem/CreditPackageItem 回归锚）；
2. 档位的价格、每周期额度、展示名、features 全部来自 plan，**不存在 legacy 回落**；
3. 只有年付 plan 的档不成立（不拿年价冒充月价）；
4. 改价即时反映；下架的档位既不列出也查不到。

需本地 PostgreSQL :15432（含 hasn_billing.billing_offering/billing_plan）。
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
from backend.app.billing.service import offering_pricing
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

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
        # 只删本用例自己造的 plan_key。doc94 D1 之后 billing_plan 是**全站档位事实源**，
        # 按 offering 整片删会把本机真实档位一起抹掉，让同批次的其它用例莫名其妙地红。
        await s.execute(
            text('DELETE FROM hasn_billing.billing_plan WHERE plan_key IN (:a, :b, :c)'),
            {'a': _TIER, 'b': f'{_TIER}_yearly', 'c': 'mk5pack'},
        )
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


async def test_catalog_is_the_only_tier_source(sess) -> None:
    """订阅档完全由商品目录描述：价格、每周期额度、展示名、features 都来自 plan。

    doc94 D1 起**不存在 legacy 回落**——`subscription_tier` 已不在读路径上。
    """
    await _ensure_offering(sess, offering_pricing.OFFERING_LLM_TIER, 'llm_tier')
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_LLM_TIER,
            plan_key=_TIER,
            price_amount=Decimal('88.00'),
            price_unit='cny',
            cycle='month',
            quota_json={'tier': _TIER, 'credits_per_cycle': '1000', 'max_agents': 3},
            display_json={'display_name': 'MK5 专业版', 'tier_name': _TIER, 'features': {'x': 1}},
            status='active',
            sort_order=1,
        )
    )
    await sess.commit()

    tier = await offering_pricing.get_tier(sess, _TIER)
    assert tier is not None
    assert tier.monthly_price == Decimal('88.00')
    assert tier.credits_per_cycle == Decimal('1000')
    assert tier.display_name == 'MK5 专业版'
    assert tier.max_agents == 3
    assert tier.features == {'x': 1}
    # 年付 plan 不存在 → 年价为空。不回落 legacy，也不拿月价冒充年价。
    assert tier.yearly_price is None

    # 本机目录里还有真实档位，这里只断言本用例的档位确实被列出（并且只出现一次）
    listed = [t.tier_name for t in await offering_pricing.list_tiers(sess)]
    assert listed.count(_TIER) == 1


async def test_missing_monthly_plan_means_tier_does_not_exist(sess) -> None:
    """只有年付 plan 的档不成立：get_tier 返回 None、list_tiers 不列出。

    否则前端会看到一个「月价 = 年价」的假档位，用户按这个价下单就是错单。
    """
    await _ensure_offering(sess, offering_pricing.OFFERING_LLM_TIER, 'llm_tier')
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_LLM_TIER,
            plan_key=f'{_TIER}_yearly',
            price_amount=Decimal('999.00'),
            price_unit='cny',
            cycle='year',
            status='active',
        )
    )
    await sess.commit()

    assert await offering_pricing.get_tier(sess, _TIER) is None
    assert _TIER not in [t.tier_name for t in await offering_pricing.list_tiers(sess)]


async def test_repricing_reflects_immediately(sess) -> None:
    """改 billing_plan.price_amount → 目录读立即反映新价（改价只影响新单）。"""
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
    tier = await offering_pricing.get_tier(sess, _TIER)
    assert tier is not None and tier.monthly_price == Decimal('88.00')

    plan.price_amount = Decimal('66.00')  # admin 商业化中心改价
    await sess.commit()
    tier = await offering_pricing.get_tier(sess, _TIER)
    assert tier is not None and tier.monthly_price == Decimal('66.00')


async def test_catalog_is_the_only_credit_pack_source(sess) -> None:
    """积分包同理：积分数、赠送、价格、描述全部来自 plan，不再读 credit_package。"""
    await _ensure_offering(sess, offering_pricing.OFFERING_CREDITS_TOPUP, 'credit_pack')
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_CREDITS_TOPUP,
            plan_key='mk5pack',
            price_amount=Decimal('45.00'),
            price_unit='cny',
            cycle='once',
            quota_json={'credits': '500', 'bonus_credits': '50'},
            display_json={'package_name': 'mk5pack', 'description': '测试包'},
            status='active',
            sort_order=1,
        )
    )
    await sess.commit()

    pack = await offering_pricing.get_credit_pack(sess, 'mk5pack')
    assert pack is not None
    assert pack.price == Decimal('45.00')
    assert pack.credits == Decimal('500')
    assert pack.bonus_credits == Decimal('50')
    assert pack.description == '测试包'

    # 下单入口按 plan 主键回查，必须拿到同一条
    by_id = await offering_pricing.get_credit_pack_by_id(sess, pack.plan_id)
    assert by_id is not None and by_id.package_name == 'mk5pack'

    assert [p.package_name for p in await offering_pricing.list_credit_packs(sess)].count('mk5pack') == 1


async def test_inactive_plan_is_invisible(sess) -> None:
    """下架的档位既不列出也查不到——下架必须真的挡住下单，而不只是前端隐藏。"""
    await _ensure_offering(sess, offering_pricing.OFFERING_CREDITS_TOPUP, 'credit_pack')
    sess.add(
        BillingPlan(
            offering_key=offering_pricing.OFFERING_CREDITS_TOPUP,
            plan_key='mk5pack',
            price_amount=Decimal('45.00'),
            price_unit='cny',
            cycle='once',
            quota_json={'credits': '500'},
            status='inactive',
        )
    )
    await sess.commit()

    assert await offering_pricing.get_credit_pack(sess, 'mk5pack') is None
    assert 'mk5pack' not in [p.package_name for p in await offering_pricing.list_credit_packs(sess)]
