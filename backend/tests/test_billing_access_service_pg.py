"""统一判定入口 resolve_access 十态矩阵真实 PG 验收（实施/92 MK-2）——零 mock。

覆盖 AccessDecision 十个规范 reason（doc02 §3.1）：
- app 路（委托 kernel）：tier_ok / need_upgrade
- 通用路（offering+entitlement+grace+quota）：free / disabled / entitled / trialing /
  need_purchase / trial_available / quota_exceeded / expired_in_grace
外加 grant_trial 发放 + 「只能一次」拦截。

需本地 PostgreSQL :15432（含商业化内核两表 + hasn_app_catalog / hasn_app_entitlement）。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.service import access_service
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

OWNER = 'h_mk2_owner_x1'


def _add_offering(sess, key: str, *, status: str = 'active', trial: bool = False, grace: int = 0) -> None:
    sess.add(
        BillingOffering(
            key=key, kind='feature_plan', feature_key=key, display_name=f'MK2 {key}',
            status=status, source='platform', sort_order=0,
        )
    )
    sess.add(
        BillingPlan(
            offering_key=key, plan_key='standard', price_amount=Decimal('9.90'), price_unit='cny', cycle='month',
            quota_json={'sites': 1},
            trial_json={'enabled': True, 'days': 7} if trial else {},
            grace_json={'grace_days': grace} if grace else {},
            status='active', sort_order=0,
        )
    )


async def _purge(sess) -> None:
    await sess.execute(text("DELETE FROM hasn_app_entitlement WHERE feature_key LIKE 'test:mk2%' OR feature_key LIKE 'app:mk2tier%'"))
    await sess.execute(text("DELETE FROM hasn_billing.billing_plan WHERE offering_key LIKE 'test:mk2%'"))
    await sess.execute(text("DELETE FROM hasn_billing.billing_offering WHERE key LIKE 'test:mk2%'"))
    await sess.execute(text("DELETE FROM hasn_app_catalog WHERE app_id LIKE 'mk2tier%'"))
    await sess.commit()


@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(s)
        now = timezone.now()
        # ── 通用路 offering ──
        _add_offering(s, 'test:mk2ent')  # 无试用；配 purchase 权益 → entitled
        _add_offering(s, 'test:mk2trial', trial=True)  # 配 trial 权益 → trialing
        _add_offering(s, 'test:mk2quota')  # 配权益 + 超额用量 → quota_exceeded
        _add_offering(s, 'test:mk2grace', grace=7)  # 配过期权益 + 宽限 → expired_in_grace
        _add_offering(s, 'test:mk2np')  # 无试用无权益 → need_purchase
        _add_offering(s, 'test:mk2ta', trial=True)  # 有试用无权益 → trial_available
        _add_offering(s, 'test:mk2dis', status='inactive')  # 下架 → disabled
        _add_offering(s, 'test:mk2gt', trial=True)  # grant_trial 用
        # 'test:mk2free' 故意不建 offering → free
        # ── 通用路权益 ──
        s.add(HasnAppEntitlement(app_id='test:mk2ent', feature_key='test:mk2ent', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={}, granted_at=now, expires_at=None))
        s.add(HasnAppEntitlement(app_id='test:mk2trial', feature_key='test:mk2trial', subject_type='owner', subject_id=OWNER, source='trial', status='active', quota_json={}, granted_at=now, expires_at=now + timedelta(days=7)))
        s.add(HasnAppEntitlement(app_id='test:mk2quota', feature_key='test:mk2quota', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={'sites': 1}, granted_at=now, expires_at=None))
        s.add(HasnAppEntitlement(app_id='test:mk2grace', feature_key='test:mk2grace', subject_type='owner', subject_id=OWNER, source='purchase', status='expired', quota_json={}, granted_at=now - timedelta(days=32), expires_at=now - timedelta(days=2)))
        # ── app 路 catalog（tier 门）──
        s.add(HasnAppCatalog(app_id='mk2tier_ok', name='MK2 tier_ok', status='published', access_type='tier', min_tier='free', scope=['personal'], purchasable_by='owner', trial_days=0))
        s.add(HasnAppCatalog(app_id='mk2tier_upg', name='MK2 need_upgrade', status='published', access_type='tier', min_tier='pro', scope=['personal'], purchasable_by='owner', trial_days=0))
        await s.commit()
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def _reason(sess, feature_key: str, *, usage: dict | None = None) -> str:
    d = await access_service.resolve_access(sess, feature_key=feature_key, subject_id=OWNER, usage=usage)
    return d.reason


# ── 通用路八态 ──
async def test_free_when_no_offering(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2free', subject_id=OWNER)
    assert d.allowed and d.reason == 'free'


async def test_disabled_when_offering_inactive(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2dis', subject_id=OWNER)
    assert not d.allowed and d.reason == 'disabled'
    assert d.offer is not None and d.offer.offering_key == 'test:mk2dis'


async def test_entitled(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2ent', subject_id=OWNER)
    assert d.allowed and d.reason == 'entitled'
    assert d.offer is not None and d.offer.purchase_uri == 'hasn://billing/offering/test:mk2ent'


async def test_trialing(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2trial', subject_id=OWNER)
    assert d.allowed and d.reason == 'trialing'


async def test_need_purchase(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2np', subject_id=OWNER)
    assert not d.allowed and d.reason == 'need_purchase' and d.requires == 'purchase'


async def test_trial_available(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2ta', subject_id=OWNER)
    assert not d.allowed and d.reason == 'trial_available' and d.trial_available is True


async def test_quota_exceeded(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2quota', subject_id=OWNER, usage={'sites': 5})
    assert not d.allowed and d.reason == 'quota_exceeded'
    assert d.quota is not None and d.quota.snapshot == {'sites': 1} and d.quota.usage == {'sites': 5}
    # 用量不超 → entitled
    d2 = await access_service.resolve_access(sess, feature_key='test:mk2quota', subject_id=OWNER, usage={'sites': 1})
    assert d2.allowed and d2.reason == 'entitled'


async def test_expired_in_grace(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='test:mk2grace', subject_id=OWNER)
    assert d.allowed and d.reason == 'expired_in_grace'
    assert d.grace is not None and d.grace.until is not None and d.grace.recoverable is True


# ── app 路两态（委托 kernel）──
async def test_tier_ok(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='app:mk2tier_ok', subject_id=OWNER)
    assert d.allowed and d.reason == 'tier_ok'


async def test_need_upgrade(sess) -> None:
    d = await access_service.resolve_access(sess, feature_key='app:mk2tier_upg', subject_id=OWNER)
    assert not d.allowed and d.reason == 'need_upgrade' and d.requires == 'upgrade'


# ── 批量 + 试用发放 ──
async def test_resolve_access_batch(sess) -> None:
    m = await access_service.resolve_access_batch(
        sess, feature_keys=frozenset({'test:mk2ent', 'test:mk2np', 'app:mk2tier_ok'}), subject_id=OWNER
    )
    assert m['test:mk2ent'].reason == 'entitled'
    assert m['test:mk2np'].reason == 'need_purchase'
    assert m['app:mk2tier_ok'].reason == 'tier_ok'


async def test_grant_trial_opens_and_blocks_second(sess) -> None:
    d = await access_service.grant_trial(sess, feature_key='test:mk2gt', subject_id=OWNER)
    assert d.allowed and d.reason == 'trialing'
    # 再次开通 → 「试用机会已用过」
    with pytest.raises(errors.RequestError):
        await access_service.grant_trial(sess, feature_key='test:mk2gt', subject_id=OWNER)


async def test_all_ten_canonical_reasons_reachable(sess) -> None:
    """一次性核对十态齐全（doc02 §3.1）。"""
    got = {
        await _reason(sess, 'test:mk2free'),
        await _reason(sess, 'test:mk2dis'),
        await _reason(sess, 'test:mk2ent'),
        await _reason(sess, 'test:mk2trial'),
        await _reason(sess, 'test:mk2np'),
        await _reason(sess, 'test:mk2ta'),
        await _reason(sess, 'test:mk2quota', usage={'sites': 9}),
        await _reason(sess, 'test:mk2grace'),
        await _reason(sess, 'app:mk2tier_ok'),
        await _reason(sess, 'app:mk2tier_upg'),
    }
    from backend.app.billing.schema.access import CANONICAL_REASONS

    assert got == set(CANONICAL_REASONS), f'十态未齐: 缺 {set(CANONICAL_REASONS) - got}, 多 {got - set(CANONICAL_REASONS)}'
