"""统一商业化内核一致性守卫·真实 PG 验收（实施/92 MK-9「一致性守卫进 CI」）——零 mock。

两道守卫，改价 / 退役 offering / 新增付费应用时若漏改配置即刻报红：
1. **feature_key 注册表校验**：全库 billing_offering.feature_key 均已在 feature_registry 注册
   （``validate_offering_consistency`` 返回空）；
2. **catalog sku_ref 悬挂检测**：hasn_app_catalog.sku_ref 若填了值必须命中真实 billing_offering.key
   （``validate_catalog_sku_refs`` 返回空）；构造一条悬挂行验证守卫真能抓到。

需本地 PostgreSQL :15432（含 hasn_billing.billing_offering / hasn_app_catalog）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import feature_registry
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio


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
