"""G3 应用权益门共享 kernel 真实 PG 验收（doc18 §4.3 · 实施/103 U3）——零 mock。

覆盖预取 map 的真实产出 + 网关付费墙回归（两面同一 kernel，口径不分叉）：
1. 付费 app 无权益 → prefetch 得 need_purchase；grant 后 → allowed；
2. 免费 app → prefetch 得 allowed（access_type=free）；
3. catalog 无此 app → 视为放行（底座工具 app_id 映射不到 catalog 行不该挂门）；
4. 网关 `_entitlement_denial` 回归（SEAT-FIX-3 锁）：付费未准入→'entitlement_denied'、
   准入/免费→None——与 kernel 同源，付费墙口径不分叉；
5. app_access_denial_reason 抽取 reason。

需本地 PostgreSQL :15432。表 hasn_app_catalog / hasn_app_entitlement 已建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service import app_access_kernel, app_catalog_service
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

PAID_APP = 'g3pg_paid_x1'
FREE_APP = 'g3pg_free_x2'
ABSENT_APP = 'g3pg_absent_x3'  # 故意不建 catalog 行
OWNER = 'h_g3pg_owner_x1'


def _agent() -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id='a_g3pg_x1',
        agent_name='g3pg',
        owner_hasn_id=OWNER,
        owner_user_id=0,
        session_uuid='amk_g3pg',
        expire_time=timezone.now(),
    )


async def _purge(db: AsyncSession) -> None:
    for app_id in (PAID_APP, FREE_APP, ABSENT_APP):
        await db.execute(text('DELETE FROM hasn_app_entitlement WHERE app_id = :a'), {'a': app_id})
        await db.execute(text('DELETE FROM hasn_app_catalog WHERE app_id = :a'), {'a': app_id})
    await db.commit()


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(sess)
        # 付费应用（purchase / personal / owner 可购）+ 免费应用，均 published。
        sess.add(
            HasnAppCatalog(
                app_id=PAID_APP, name='G3 付费测试应用', status='published',
                access_type='purchase', scope=['personal'], purchasable_by='owner',
                billing_cycle='month',
            )
        )
        sess.add(
            HasnAppCatalog(
                app_id=FREE_APP, name='G3 免费测试应用', status='published',
                access_type='free', scope=['personal'], purchasable_by='owner',
            )
        )
        await sess.commit()
        yield sess
    finally:
        await _purge(sess)
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_paid_without_entitlement_need_purchase(session: AsyncSession) -> None:
    """付费 app 无权益：kernel 合并准入 → need_purchase，denial_reason 抽取一致。"""
    access = await app_access_kernel.resolve_merged_app_access(
        session, app_id=PAID_APP, owner_hasn_id=OWNER, active_enterprise_id=None
    )
    assert access['allowed'] is False
    assert access['reason'] == 'need_purchase'
    assert app_access_kernel.app_access_denial_reason(access) == 'need_purchase'


async def test_prefetch_map_reflects_gate_set(session: AsyncSession) -> None:
    """批量预取 map：付费未购 → 拒；免费 → 准入；缺 catalog → 放行（底座工具兜底）。"""
    m = await app_access_kernel.prefetch_app_access_map(
        session, owner_hasn_id=OWNER, active_enterprise_id=None,
        app_ids=frozenset({PAID_APP, FREE_APP, ABSENT_APP}),
    )
    assert m[PAID_APP]['allowed'] is False
    assert m[PAID_APP]['reason'] == 'need_purchase'
    assert m[FREE_APP]['allowed'] is True
    assert m[ABSENT_APP]['allowed'] is True  # 不在 catalog → never over-block


async def test_grant_entitlement_flips_to_allowed(session: AsyncSession) -> None:
    """owner 获授权益后：kernel 合并准入 → allowed（entitled），denial_reason=None。"""
    cat = await app_catalog_service.get_catalog(session, app_id=PAID_APP)
    assert cat is not None
    await app_catalog_service.grant_entitlement(
        session, app_id=PAID_APP, subject_type='owner', subject_id=OWNER, source='admin'
    )
    await session.commit()

    access = await app_access_kernel.resolve_merged_app_access(
        session, app_id=PAID_APP, owner_hasn_id=OWNER, active_enterprise_id=None
    )
    assert access['allowed'] is True
    assert app_access_kernel.app_access_denial_reason(access) is None


async def test_free_app_allowed(session: AsyncSession) -> None:
    """免费 app：合并准入 allowed（access_type=free）。"""
    access = await app_access_kernel.resolve_merged_app_access(
        session, app_id=FREE_APP, owner_hasn_id=OWNER, active_enterprise_id=None
    )
    assert access['allowed'] is True
    assert app_access_kernel.app_access_denial_reason(access) is None


# ── 网关付费墙回归：两面同一 kernel，口径不分叉（SEAT-FIX-3 锁不变） ────────


async def test_gateway_entitlement_denial_matches_kernel(session: AsyncSession) -> None:
    """网关 `_entitlement_denial` 收编后仍：付费未购→'entitlement_denied'、免费/准入→None。"""
    agent = _agent()
    # 付费未购 → 拒（折叠为审计口径 entitlement_denied）
    denial = await ai_native_runtime_gateway._entitlement_denial(session, app_id=PAID_APP, agent=agent)
    assert denial == 'entitlement_denied'
    # 免费 → 放行（free 快路短路）
    assert await ai_native_runtime_gateway._entitlement_denial(session, app_id=FREE_APP, agent=agent) is None
    # catalog 无此 app → 放行
    assert await ai_native_runtime_gateway._entitlement_denial(session, app_id=ABSENT_APP, agent=agent) is None

    # 授权后 → 放行（kernel 与网关同源）
    await app_catalog_service.grant_entitlement(
        session, app_id=PAID_APP, subject_type='owner', subject_id=OWNER, source='admin'
    )
    await session.commit()
    assert await ai_native_runtime_gateway._entitlement_denial(session, app_id=PAID_APP, agent=agent) is None
