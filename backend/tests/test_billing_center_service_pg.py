"""费用与账单中心聚合 billing_center_service.get_center 真实 PG 验收（实施/92 MK-7）——零 mock。

覆盖：
- 订阅+积分快照透出（复用 credit_service.get_user_credits_info 原语字典）；
- 权益总账五态实时重算（active / trialing / in_grace / expired / revoked，读时诚实、不信 DB 存量 status）；
- 提醒条（expiring：到期临近；in_grace：已过期但在宽限期内）。

需本地 PostgreSQL :15432（含商业化内核两表 + hasn_app_entitlement + hasn_humans + billing 订阅表）。
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
from backend.app.billing.service.billing_center_service import billing_center_service
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 用一个几乎不可能撞库的测试 user_id / owner
TEST_USER_ID = 990199
OWNER = 'h_mkc_owner_x1'


def _add_offering(sess, key: str, *, grace_json: dict | None = None, trial: bool = False) -> None:
    sess.add(
        BillingOffering(
            key=key, kind='feature_plan', feature_key=key, display_name=f'账单中心 {key}',
            status='active', source='platform', sort_order=0,
        )
    )
    sess.add(
        BillingPlan(
            offering_key=key, plan_key='standard', price_amount=Decimal('9.90'), price_unit='cny', cycle='month',
            quota_json={'sites': 1},
            trial_json={'enabled': True, 'days': 7} if trial else {},
            grace_json=grace_json or {},
            status='active', sort_order=0,
        )
    )


async def _purge(sess) -> None:
    await sess.execute(text("DELETE FROM hasn_app_entitlement WHERE feature_key LIKE 'test:mkc%'"))
    await sess.execute(text("DELETE FROM hasn_billing.billing_plan WHERE offering_key LIKE 'test:mkc%'"))
    await sess.execute(text("DELETE FROM hasn_billing.billing_offering WHERE key LIKE 'test:mkc%'"))
    await sess.execute(text('DELETE FROM hasn_humans WHERE user_id = :uid'), {'uid': TEST_USER_ID})
    await sess.execute(text('DELETE FROM hasn_billing.user_subscription WHERE user_id = :uid'), {'uid': TEST_USER_ID})
    await sess.execute(text('DELETE FROM hasn_billing.user_credit_balance WHERE user_id = :uid'), {'uid': TEST_USER_ID})
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
        # owner 身份映射（user_id → hasn_id）
        s.add(HasnHumans(hasn_id=OWNER, star_id='mkc990199', user_id=TEST_USER_ID, nickname='账单中心测试主人', status='active'))

        # ── offering + plan（宽限/提醒策略挂默认档 grace_json）──
        _add_offering(s, 'test:mkc_act')  # 无到期 → active，无提醒
        _add_offering(s, 'test:mkc_tri', trial=True)  # 试用远期到期 → trialing，无提醒
        _add_offering(s, 'test:mkc_exp', grace_json={'grace_days': 7, 'remind_days': [7, 3, 1]})  # 3天后到期 → active + expiring
        _add_offering(s, 'test:mkc_grc', grace_json={'grace_days': 7, 'remind_days': [7, 3, 1]})  # 过期2天在宽限 → in_grace
        _add_offering(s, 'test:mkc_old', grace_json={'grace_days': 7})  # 过期40天出宽限 → expired
        _add_offering(s, 'test:mkc_rev')  # DB status=revoked → revoked

        # ── 权益行 ──
        s.add(HasnAppEntitlement(app_id='test:mkc_act', feature_key='test:mkc_act', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={}, granted_at=now, expires_at=None))
        s.add(HasnAppEntitlement(app_id='test:mkc_tri', feature_key='test:mkc_tri', subject_type='owner', subject_id=OWNER, source='trial', status='active', quota_json={}, granted_at=now, expires_at=now + timedelta(days=30)))
        s.add(HasnAppEntitlement(app_id='test:mkc_exp', feature_key='test:mkc_exp', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={}, granted_at=now - timedelta(days=27), expires_at=now + timedelta(days=3)))
        s.add(HasnAppEntitlement(app_id='test:mkc_grc', feature_key='test:mkc_grc', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={}, granted_at=now - timedelta(days=32), expires_at=now - timedelta(days=2)))
        s.add(HasnAppEntitlement(app_id='test:mkc_old', feature_key='test:mkc_old', subject_type='owner', subject_id=OWNER, source='purchase', status='active', quota_json={}, granted_at=now - timedelta(days=70), expires_at=now - timedelta(days=40)))
        s.add(HasnAppEntitlement(app_id='test:mkc_rev', feature_key='test:mkc_rev', subject_type='owner', subject_id=OWNER, source='purchase', status='revoked', quota_json={}, granted_at=now - timedelta(days=5), expires_at=None))
        await s.commit()
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_center_subscription_snapshot(sess) -> None:
    """概览带订阅+积分快照（全 JSON 安全原语）。"""
    resp = await billing_center_service.get_center(sess, user_id=TEST_USER_ID)
    assert resp.subscription
    assert resp.subscription['user_id'] == TEST_USER_ID
    # 关键原语字段存在且为 JSON 安全类型
    # doc94 F3/D1：余额来自 NewAPI 权威快照。本用例的测试用户没有 NewAPI 映射，
    # 于是余额为 None + credit_status='unmapped'——**不许**回落云端旧值或伪造 0。
    assert resp.subscription['current_credits'] is None
    assert resp.subscription['credit_status'] == 'unmapped'
    assert 'tier' in resp.subscription and 'status' in resp.subscription


async def test_center_entitlement_five_states(sess) -> None:
    """权益总账五态实时重算齐全。"""
    resp = await billing_center_service.get_center(sess, user_id=TEST_USER_ID)
    by_fk = {e.feature_key: e for e in resp.entitlements}
    assert by_fk['test:mkc_act'].status == 'active'
    assert by_fk['test:mkc_tri'].status == 'trialing'
    assert by_fk['test:mkc_exp'].status == 'active'
    assert by_fk['test:mkc_grc'].status == 'in_grace'
    assert by_fk['test:mkc_old'].status == 'expired'
    assert by_fk['test:mkc_rev'].status == 'revoked'
    # in_grace 行带宽限截止；其余无
    assert by_fk['test:mkc_grc'].grace_until is not None
    assert by_fk['test:mkc_old'].grace_until is None
    # display_name 取自 offering
    assert by_fk['test:mkc_act'].display_name == '账单中心 test:mkc_act'
    assert by_fk['test:mkc_act'].offering_kind == 'feature_plan'


async def test_center_reminders(sess) -> None:
    """提醒条：到期临近（expiring）+ 宽限期中（in_grace）。"""
    resp = await billing_center_service.get_center(sess, user_id=TEST_USER_ID)
    reminders = {r.feature_key: r for r in resp.reminders}
    # 3天后到期、在 remind_days=[7,3,1] 窗内 → expiring
    # days_left 是 floor 天数：到期设 now+3d，读时 now 已略推进 → floor(2天23h..) = 2
    assert 'test:mkc_exp' in reminders
    assert reminders['test:mkc_exp'].kind == 'expiring'
    assert 2 <= reminders['test:mkc_exp'].days_left <= 3
    # 宽限期中 → in_grace
    assert 'test:mkc_grc' in reminders
    assert reminders['test:mkc_grc'].kind == 'in_grace'
    # 远期到期 / 无到期 / 已过期出宽限 → 不提醒
    assert 'test:mkc_tri' not in reminders
    assert 'test:mkc_act' not in reminders
    assert 'test:mkc_old' not in reminders


async def test_center_no_owner_returns_subscription_only(sess) -> None:
    """无 owner 身份（异常态）→ 只回订阅快照，权益/提醒留空。"""
    # 一个没有 hasn_humans 映射的 user_id
    resp = await billing_center_service.get_center(sess, user_id=990198)
    assert resp.subscription  # 订阅快照仍在（get_or_create 自建）
    assert resp.entitlements == []
    assert resp.reminders == []
    # 清理该临时用户订阅
    await sess.execute(text('DELETE FROM hasn_billing.user_subscription WHERE user_id = 990198'))
    await sess.execute(text('DELETE FROM hasn_billing.user_credit_balance WHERE user_id = 990198'))
    await sess.commit()
