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
    '2026-07-14-workflow-template.sql',
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
