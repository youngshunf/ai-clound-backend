"""统一商业化内核 MK-1 数据层真实 PG 验收（实施/92 MK-1）——零 mock。

覆盖施工清单 MK-1「pytest 真 PG」四项：
1. 建表/加列到位：billing_offering / billing_plan 两表 + hasn_app_entitlement 加列
   feature_key/quota_json + pay_order 加列 offering_ref；
2. 种子幂等重跑：再次执行 seed，offering/plan 计数不变（ON CONFLICT DO NOTHING）；
3. feature_registry 注册表校验：固定键/前缀族命中、垃圾键不命中、
   全库 offering.feature_key 均已注册（validate_offering_consistency 返回空）；
4. 快照字段形状：plan.quota_json / trial_json 为 dict，llm:tier 档带 tier 快照键。

需本地 PostgreSQL :15432。测试自身幂等应用四支 SQL（建表+加列+seed），可在裸库跑。
"""

from __future__ import annotations

import pathlib

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import feature_registry
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# backend/ 根（tests/ 的上一级）
_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_SQL_FILES = (
    _BACKEND / 'sql' / 'billing' / 'billing_kernel.sql',
    _BACKEND / 'sql' / 'hasn' / 'migrations' / '2026-07-10-billing-kernel-entitlement-cols.sql',
    _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-10-billing-kernel-payorder-offering-ref.sql',
)
_SEED_FILE = _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-10-billing-kernel-seed.sql'


def _statements(sql_path: pathlib.Path) -> list[str]:
    """把 .sql 文件切成单条语句（剔除整行注释）。语句内无分号，按 ';' 切安全。"""
    raw = sql_path.read_text(encoding='utf-8')
    body = '\n'.join(ln for ln in raw.splitlines() if not ln.lstrip().startswith('--'))
    return [s.strip() for s in body.split(';') if s.strip()]


async def _apply_sql(engine, sql_path: pathlib.Path) -> None:
    """逐条执行 .sql；用 exec_driver_sql 绕开 SQLAlchemy 对 'llm:tier' 里冒号的 bindparam 误解析。"""
    async with engine.begin() as conn:
        for stmt in _statements(sql_path):
            await conn.exec_driver_sql(stmt)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    # 幂等应用建表 + 加列 + seed（裸库可跑）
    for p in (*_SQL_FILES, _SEED_FILE):
        await _apply_sql(engine, p)
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess, engine
    finally:
        await sess.close()
        await engine.dispose()
        await async_engine.dispose()


async def _columns(sess, schema: str, table: str) -> set[str]:
    rows = await sess.execute(
        text(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_schema = :s AND table_name = :t'
        ),
        {'s': schema, 't': table},
    )
    return {r[0] for r in rows}


async def test_kernel_tables_and_columns(ctx) -> None:
    """两表列齐 + entitlement 加列 + pay_order 加列。"""
    sess, _ = ctx
    offering_cols = await _columns(sess, 'hasn_billing', 'billing_offering')
    assert {'key', 'kind', 'feature_key', 'display_name', 'status', 'source', 'sort_order'} <= offering_cols

    plan_cols = await _columns(sess, 'hasn_billing', 'billing_plan')
    assert {'offering_key', 'plan_key', 'price_amount', 'price_unit', 'cycle',
            'quota_json', 'trial_json', 'grace_json', 'status'} <= plan_cols

    ent_cols = await _columns(sess, 'public', 'hasn_app_entitlement')
    assert {'feature_key', 'quota_json'} <= ent_cols, 'entitlement 未加 feature_key/quota_json 列'

    order_cols = await _columns(sess, 'hasn_billing', 'pay_order')
    assert 'offering_ref' in order_cols, 'pay_order 未加 offering_ref 列'


async def test_seed_idempotent_rerun(ctx) -> None:
    """再跑 seed，offering/plan 计数不变（ON CONFLICT DO NOTHING）。"""
    sess, engine = ctx
    before_off = (await sess.execute(select(func.count()).select_from(BillingOffering))).scalar_one()
    before_plan = (await sess.execute(select(func.count()).select_from(BillingPlan))).scalar_one()
    assert before_off >= 1 and before_plan >= 1, 'seed 应至少产出内核 offering/plan（本地库需有存量定价）'

    await _apply_sql(engine, _SEED_FILE)  # 幂等重跑

    after_off = (await sess.execute(select(func.count()).select_from(BillingOffering))).scalar_one()
    after_plan = (await sess.execute(select(func.count()).select_from(BillingPlan))).scalar_one()
    assert after_off == before_off, f'重跑后 offering 计数漂移 {before_off}->{after_off}'
    assert after_plan == before_plan, f'重跑后 plan 计数漂移 {before_plan}->{after_plan}'


async def test_feature_registry_membership() -> None:
    """注册表命中判定：固定键 + 前缀族命中，垃圾键不命中。"""
    assert feature_registry.is_registered('llm:tier')
    assert feature_registry.is_registered('credits:topup')
    assert feature_registry.is_registered('webapp:hosting')
    # cloud_node：云端常驻节点（无头 hasn-node 容器），与上面托管网页应用的 webapp:hosting 是两码事
    assert feature_registry.is_registered('cloud_node')
    assert feature_registry.is_registered('app:deck')  # 前缀族 app:<id>
    assert feature_registry.is_registered('seat:quant')  # 前缀族 seat:<id>
    assert not feature_registry.is_registered('app:')  # 前缀无实例段 → 非法
    assert not feature_registry.is_registered('bogus:xyz')
    assert not feature_registry.is_registered('')
    # 批量校验：混入一个非法键，只回非法者
    assert feature_registry.validate_feature_keys(['llm:tier', 'nope:1', 'app:deck']) == ['nope:1']


async def test_offering_consistency_no_violation(ctx) -> None:
    """全库 offering.feature_key 均已注册（种子产物合规）。"""
    sess, _ = ctx
    violations = await feature_registry.validate_offering_consistency(sess)
    assert violations == [], f'存在未注册 feature_key 的 offering: {violations}'


async def test_snapshot_field_shape(ctx) -> None:
    """快照字段形状：plan 三个 json 列为 dict；llm:tier 档 quota 带 tier 快照键。"""
    sess, _ = ctx
    plans = (await sess.execute(select(BillingPlan).where(BillingPlan.offering_key == 'llm:tier'))).scalars().all()
    if not plans:  # 裸库无存量订阅档时不强断言（数据层机制已由前序用例覆盖）
        pytest.skip('本地库无 llm:tier 存量档，跳过快照形状断言')
    for plan in plans:
        assert isinstance(plan.quota_json, dict)
        assert isinstance(plan.trial_json, dict)
        assert isinstance(plan.grace_json, dict)
        assert 'tier' in plan.quota_json, f'{plan.plan_key} quota 缺 tier 快照键'
