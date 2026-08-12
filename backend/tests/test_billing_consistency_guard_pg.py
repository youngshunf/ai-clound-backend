"""统一商业化内核一致性守卫·真实 PG 验收（实施/92 MK-9「一致性守卫进 CI」）——零 mock。

两道守卫，改价 / 退役 offering / 新增付费应用时若漏改配置即刻报红：
1. **feature_key 注册表校验**：全库 billing_offering.feature_key 均已在 feature_registry 注册
   （``validate_offering_consistency`` 返回空）；
2. **catalog sku_ref 悬挂检测**：hasn_app_catalog.sku_ref 若填了值必须命中真实 billing_offering.key
   （``validate_catalog_sku_refs`` 返回空）；构造一条悬挂行验证守卫真能抓到。

需本地 PostgreSQL :15432（含 hasn_billing.billing_offering / hasn_app_catalog）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import feature_registry
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn_task.model.workflow_template import HasnWorkflowTemplate
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio

# workflow_template 建表迁移链（幂等）：守卫需 hasn_task.workflow_template 表在位才能扫。
# 与 tests/hasn_task/test_workflow_template_tools.py 的 env 迁移链一致（TEMPLATE 依赖 workflow 表）。
_WF_SQL_DIR = Path(__file__).resolve().parent / 'hasn_task'
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / 'sql' / 'hasn_task' / 'migrations'
_WF_MIGRATION_CHAIN = (
    '2026-06-10-ainative-refactor.sql',
    '2026-06-11-workflow.sql',
    '2026-07-14-workflow-node-tables.sql',
    '2026-07-14-workflow-run-advance-mode.sql',
    '2026-07-26-workflow-history-recovery.sql',
    '2026-07-14-workflow-template.sql',
    '2026-07-29-workflow-template-source-release.sql',
)

_TIER_REDEFINE_MIGRATION = (
    Path(__file__).resolve().parent.parent / 'sql' / 'billing' / 'migrations' / '2026-08-12-tier-five-plan-redefine.sql'
)


async def _ensure_workflow_template_table() -> None:
    """幂等 bootstrap：确保 hasn_task.workflow_template 表在位（独立 asyncpg 连接执行 DDL，自动提交）。"""
    import asyncpg

    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace(
        'postgresql+asyncpg://', 'postgresql://'
    )
    conn = await asyncpg.connect(dsn)
    try:
        for name in _WF_MIGRATION_CHAIN:
            await conn.execute((_MIGRATIONS_DIR / name).read_text(encoding='utf-8'))
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def sess() -> AsyncIterator[AsyncSession]:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        connection = await s.connection()
        raw_connection = await connection.get_raw_connection()
        await raw_connection.driver_connection.execute(_TIER_REDEFINE_MIGRATION.read_text(encoding='utf-8'))
        yield s
    finally:
        # 全程只 flush 不 commit，rollback 即隔离（构造的悬挂 catalog 行不落库）。
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


# ── 守卫 1：feature_key 注册表 ──
async def test_offering_feature_keys_all_registered(sess: AsyncSession) -> None:
    """全库 offering.feature_key 均已注册（种子 + 存量合规）。"""
    violations = await feature_registry.validate_offering_consistency(sess)
    assert violations == [], f'存在未注册 feature_key 的 offering: {violations}'


async def test_llm_tier_catalog_matches_five_plan_policy(sess: AsyncSession) -> None:
    """订阅目录只有五个稳定档位，卡片数字权益与配额一致。"""
    violations = await feature_registry.validate_tier_catalog_consistency(sess)
    assert violations == [], f'订阅档位目录与定档事实源不一致: {violations}'


async def test_llm_tier_feature_guard_detects_quota_drift() -> None:
    """守卫自证：卡片写 5000 积分、实际只发 2000 时必须报错。"""
    violations = feature_registry.validate_tier_plan_payload(
        plan_key='max',
        quota_json={
            'credits_per_cycle': '2000.00000',
            'storage_bytes': 536870912000,
            'max_agents': 10,
        },
        display_json={
            'features': {
                '每月积分': 5000,
                '云存储': '500 GB',
                '分身数量': 10,
                '客服支持': '优先支持',
            }
        },
    )
    assert any('每月积分' in item for item in violations)


async def test_five_plan_migration_is_idempotent_and_exact() -> None:
    """真实 PostgreSQL 事务内连跑两次迁移，验证五档与旧键退役。"""
    import asyncpg

    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace(
        'postgresql+asyncpg://', 'postgresql://'
    )
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    tx = conn.transaction()
    await tx.start()
    try:
        sql = _TIER_REDEFINE_MIGRATION.read_text(encoding='utf-8')
        await conn.execute(sql)
        await conn.execute(sql)

        active_monthly = await conn.fetch(
            """
            SELECT plan_key,
                   price_amount::text,
                   quota_json->>'credits_per_cycle' AS credits_per_cycle,
                   display_json->>'display_name' AS display_name
              FROM hasn_billing.billing_plan
             WHERE offering_key = 'llm:tier'
               AND status = 'active'
               AND right(plan_key, 7) <> '_yearly'
             ORDER BY sort_order, plan_key
            """
        )
        assert [row['plan_key'] for row in active_monthly] == ['free', 'lite', 'pro', 'max', 'ultra']
        assert [row['display_name'] for row in active_monthly] == [
            '免费版',
            '轻享版',
            '专业版',
            '高级版',
            '旗舰版',
        ]
        assert [row['price_amount'] for row in active_monthly] == [
            '0.00',
            '49.00',
            '99.00',
            '299.00',
            '699.00',
        ]
        assert [row['credits_per_cycle'] for row in active_monthly] == [
            '100.00000',
            '500.00000',
            '1200.00000',
            '4000.00000',
            '10000.00000',
        ]

        old_active = await conn.fetchval(
            """
            SELECT count(*)
              FROM hasn_billing.billing_plan
             WHERE offering_key = 'llm:tier'
               AND status = 'active'
               AND (plan_key = ANY(ARRAY['advanced', 'flagship'])
                    OR plan_key = ANY(ARRAY['advanced_yearly', 'flagship_yearly']))
            """
        )
        assert old_active == 0
    finally:
        await tx.rollback()
        await conn.close()


# ── 守卫 2：catalog sku_ref 悬挂检测 ──
async def test_catalog_sku_refs_no_dangling(sess: AsyncSession) -> None:
    """全库 catalog.sku_ref 无悬挂（空值常态 + 已填值命中真实 offering）。"""
    violations = await feature_registry.validate_catalog_sku_refs(sess)
    assert violations == [], f'存在悬挂 sku_ref 的 catalog 行: {violations}'


async def test_catalog_sku_ref_dangling_is_detected(sess: AsyncSession) -> None:
    """守卫真能抓到：造一条 sku_ref 指向不存在 offering 的 catalog 行 → 被列为违规。"""
    bogus = HasnAppCatalog(
        app_id='mk9_dangling_guard_probe',
        name='MK9 悬挂守卫探针',
        status='draft',
        access_type='free',
        scope=['personal'],
        sku_ref='offering_that_does_not_exist_mk9',
    )
    sess.add(bogus)
    await sess.flush()  # flush 后同 session 查询可见；不 commit，teardown rollback 清掉

    violations = await feature_registry.validate_catalog_sku_refs(sess)
    assert any('mk9_dangling_guard_probe' in v for v in violations), f'守卫未抓到悬挂 sku_ref: {violations}'

    # 空 sku_ref 不误报：把探针 sku_ref 清空后应从违规列表消失。
    bogus.sku_ref = None
    await sess.flush()
    violations2 = await feature_registry.validate_catalog_sku_refs(sess)
    assert not any('mk9_dangling_guard_probe' in v for v in violations2), f'空 sku_ref 被误报为悬挂: {violations2}'


# ── 守卫 3（MK-9b·doc94 §10-P7）：workflow_template sku_ref 悬挂检测 ──
async def test_workflow_template_sku_refs_no_dangling(sess: AsyncSession) -> None:
    """全库 workflow_template.sku_ref 无悬挂（空值常态 + 已填值命中真实 offering）。"""
    await _ensure_workflow_template_table()
    violations = await feature_registry.validate_workflow_template_sku_refs(sess)
    assert violations == [], f'存在悬挂 sku_ref 的 workflow_template 行: {violations}'


async def test_workflow_template_sku_ref_dangling_is_detected(sess: AsyncSession) -> None:
    """守卫真能抓到：造一条 sku_ref 指向不存在 offering 的模板行 → 被列为违规；清空后不误报。"""
    await _ensure_workflow_template_table()
    key = 'mk9b_wf_dangling_probe'
    bogus = HasnWorkflowTemplate(
        template_uuid=f'wft_{key}',
        template_key=key,
        name='MK9b 工作流模板悬挂探针',
        status='draft',
        graph_spec={},
        is_builtin=False,
        source='owner',
        version=1,
        sku_ref='wf_offering_that_does_not_exist_mk9b',
    )
    sess.add(bogus)
    await sess.flush()  # flush 后同 session 查询可见；不 commit，teardown rollback 清掉

    violations = await feature_registry.validate_workflow_template_sku_refs(sess)
    assert any(key in v for v in violations), f'守卫未抓到悬挂 sku_ref: {violations}'

    # 空 sku_ref 不误报：付费模板转免费（sku_ref 清空）后应从违规列表消失。
    bogus.sku_ref = None
    await sess.flush()
    violations2 = await feature_registry.validate_workflow_template_sku_refs(sess)
    assert not any(key in v for v in violations2), f'空 sku_ref 被误报为悬挂: {violations2}'
