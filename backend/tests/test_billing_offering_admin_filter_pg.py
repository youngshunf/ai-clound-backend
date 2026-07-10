"""MK-6 商业化中心管理面·offering/plan 列表检索过滤·真实 PG（实施/92 MK-6）——零 mock。

覆盖 admin 商业化中心「商品目录/价格档位」列表页的后端检索契约：
1. offering 列表按 kind 精确过滤、按 key 子串模糊过滤（可叠加）；
2. plan 列表按 offering_key 精确过滤（查某商品全部档位）、按 status 精确过滤；
3. 无过滤参数时返回全量（分页）。

需本地 PostgreSQL :15432（含 hasn_billing.billing_offering / billing_plan）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.crud.crud_billing_offering import billing_offering_dao
from backend.app.billing.crud.crud_billing_plan import billing_plan_dao
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 测试前缀（隔离，_purge 只清本前缀，绝不误伤存量种子）
_PFX = 'mk6flt:'
_OFF_APP = f'{_PFX}app:demo'
_OFF_TIER = f'{_PFX}llm:tier'
_OFF_PACK = f'{_PFX}credit:pack'


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
            text('DELETE FROM hasn_billing.billing_plan WHERE offering_key LIKE :p'),
            {'p': f'{_PFX}%'},
        )
        await s.execute(
            text('DELETE FROM hasn_billing.billing_offering WHERE key LIKE :p'),
            {'p': f'{_PFX}%'},
        )
        await s.commit()

    try:
        await _purge()
        # 种三个 offering：两种 kind，key 含可区分子串
        s.add_all([
            BillingOffering(key=_OFF_APP, kind='app', feature_key=_OFF_APP, display_name='演示应用', status='active'),
            BillingOffering(
                key=_OFF_TIER, kind='llm_tier', feature_key=_OFF_TIER, display_name='演示LLM档', status='active'
            ),
            BillingOffering(
                key=_OFF_PACK, kind='credit_pack', feature_key=_OFF_PACK, display_name='演示积分包', status='inactive'
            ),
        ])
        # 种四个 plan：TIER 下两档（一 active 一 inactive），APP 下一档，PACK 下一档
        s.add_all([
            BillingPlan(
                offering_key=_OFF_TIER,
                plan_key='monthly',
                price_amount=Decimal('88.00'),
                price_unit='cny',
                cycle='month',
                status='active',
            ),
            BillingPlan(
                offering_key=_OFF_TIER,
                plan_key='yearly',
                price_amount=Decimal('888.00'),
                price_unit='cny',
                cycle='year',
                status='inactive',
            ),
            BillingPlan(
                offering_key=_OFF_APP,
                plan_key='standard',
                price_amount=Decimal('30.00'),
                price_unit='cny',
                cycle='month',
                status='active',
            ),
            BillingPlan(
                offering_key=_OFF_PACK,
                plan_key='once',
                price_amount=Decimal('45.00'),
                price_unit='cny',
                cycle='once',
                status='active',
            ),
        ])
        await s.commit()
        yield s
    finally:
        await _purge()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def _off_keys(s, **flt) -> set[str]:
    """跑 offering DAO get_select 返回的查询表达式，取本测试前缀 key 集合（隔离存量）。"""
    stmt = await billing_offering_dao.get_select(**flt)
    rows = (await s.execute(stmt)).scalars().all()
    return {r.key for r in rows if r.key.startswith(_PFX)}


async def _plan_keys(s, **flt) -> set[tuple[str, str]]:
    stmt = await billing_plan_dao.get_select(**flt)
    rows = (await s.execute(stmt)).scalars().all()
    return {(r.offering_key, r.plan_key) for r in rows if r.offering_key.startswith(_PFX)}


# ── 1. offering 列表过滤（DAO get_select，管理面检索契约）──
async def test_offering_filter_by_kind(sess) -> None:
    assert await _off_keys(sess, kind='app') == {_OFF_APP}, 'kind=app 只应命中演示应用'


async def test_offering_filter_by_key_substring(sess) -> None:
    assert await _off_keys(sess, key='llm:tier') == {_OFF_TIER}, 'key 子串应模糊命中 LLM 档'


async def test_offering_filter_combined_kind_and_key(sess) -> None:
    # kind=credit_pack 且 key 含前缀 → 只命中积分包
    assert await _off_keys(sess, kind='credit_pack', key=_PFX) == {_OFF_PACK}


async def test_offering_no_filter_returns_all_seeded(sess) -> None:
    assert await _off_keys(sess) == {_OFF_APP, _OFF_TIER, _OFF_PACK}, '无过滤应含全部本测试 offering'


# ── 2. plan 列表过滤 ──
async def test_plan_filter_by_offering_key(sess) -> None:
    assert await _plan_keys(sess, offering_key=_OFF_TIER) == {
        (_OFF_TIER, 'monthly'),
        (_OFF_TIER, 'yearly'),
    }, '按 offering 分组查看其全部档位'


async def test_plan_filter_by_status(sess) -> None:
    assert await _plan_keys(sess, status='inactive') == {(_OFF_TIER, 'yearly')}, 'status=inactive 只命中年付下架档'


async def test_plan_filter_combined_offering_and_status(sess) -> None:
    assert await _plan_keys(sess, offering_key=_OFF_TIER, status='active') == {(_OFF_TIER, 'monthly')}, (
        'TIER 下 active 档只剩月付'
    )
