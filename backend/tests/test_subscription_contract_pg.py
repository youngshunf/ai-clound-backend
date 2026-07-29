"""doc94 F2 订阅合同真实 PostgreSQL 验收。

锁住的是合同生命周期里最容易出错的四条规则：

1. 月付 = 1 个 30 天周期，年付 = 12 个连续 30 天周期（360 天）——绝不使用自然月；
2. 同档续费排在旧合同之后（``scheduled``），到点由 NewAPI 原子切换，期间不得提前消费；
3. 升级立即生效并当场终止旧合同；降级下周期生效（立即降级会砍掉用户已付费的这一期额度）；
4. 取消自动续费只改合同状态，**不提前清空额度**。

需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import pathlib
import uuid

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service.contract_status import STATUS_CANCEL_AT_PERIOD_END
from backend.app.billing.service.credit_grant_event_service import (
    CYCLE_SECONDS,
    EVENT_SUBSCRIPTION_ACTIVATE,
    EVENT_SUBSCRIPTION_EXPIRE,
)
from backend.app.billing.service.subscription_contract_service import subscription_contract_service
from backend.database.db import async_db_session
from backend.tests.billing_catalog_seed import CatalogSeed

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_CREDIT_EVENT_SQL = _BACKEND / 'sql' / 'billing' / 'credit_grant_event.sql'
_CONTRACT_MIGRATION = _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-25-credit-authority-contract-and-outbox.sql'
# doc94 D1：档位事实源迁到商品目录，plan 需要 display_json 列
_DISPLAY_MIGRATION = _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-25-credit-authority-plan-display-migrate.sql'
_APP_CODE = 'doc94f2'
_GIB = 1024**3
_PRO_STORAGE_BYTES = 100 * _GIB
_FLAGSHIP_STORAGE_BYTES = 500 * _GIB


class _Order:
    """支付订单的数据 double（只带履约需要的字段）。"""

    def __init__(self, *, user_id: int, tier: str, billing_cycle: str) -> None:
        self.order_no = f'HXF2{uuid.uuid4().hex[:12].upper()}'
        self.user_id = user_id
        self.target_tier = tier
        self.billing_cycle = billing_cycle
        self.extra_data = {'app_code': _APP_CODE}
        self.offering_ref = {'offering_key': 'llm:tier', 'plan_key': tier, 'kind': 'llm_tier'}
        self.fulfillment_status = 'not_required'
        self.fulfillment_event_id = None


async def _apply_sql(conn, path: pathlib.Path) -> None:
    raw = path.read_text(encoding='utf-8')
    for stmt in (s.strip() for s in raw.split(';')):
        body = '\n'.join(ln for ln in stmt.splitlines() if not ln.lstrip().startswith('--'))
        if body.strip():
            await conn.exec_driver_sql(body)


@pytest_asyncio.fixture
async def user_id(monkeypatch) -> AsyncIterator[int]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

    await async_engine.dispose()
    ddl_engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with ddl_engine.begin() as conn:
            await _apply_sql(conn, _CREDIT_EVENT_SQL)
            await _apply_sql(conn, _CONTRACT_MIGRATION)
            await _apply_sql(conn, _DISPLAY_MIGRATION)
    except Exception as exc:
        await ddl_engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    await ddl_engine.dispose()

    uid = 960_000_000 + int(uuid.uuid4().hex[:6], 16) % 1_000_000

    async def _fake_resolve(db, user, app_code):  # noqa: ANN001
        return 700_000 + (user % 1000)

    import backend.app.billing.service.pay_callbacks as callbacks_module

    monkeypatch.setattr(callbacks_module, '_resolve_newapi_user_id', _fake_resolve)

    # 两个档位：pro(sort_order=10) < flagship(sort_order=30)，用于升/降级判定。
    # doc94 D1 起档位事实源是商品目录 billing_plan，不再是 subscription_tier。
    seed = CatalogSeed()
    async with async_db_session.begin() as db:
        for tier, credits, order, storage_bytes in (
            ('pro', 1000, 10, _PRO_STORAGE_BYTES),
            ('flagship', 10000, 30, _FLAGSHIP_STORAGE_BYTES),
        ):
            await seed.seed_tier(
                db,
                tier_name=tier,
                credits_per_cycle=credits,
                sort_order=order,
                storage_bytes=storage_bytes,
            )
    try:
        yield uid
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': uid})
            await db.execute(text('DELETE FROM hasn_billing.user_subscription WHERE app_code = :a'), {'a': _APP_CODE})
            await seed.restore(db)
        await async_engine.dispose()


async def _activate(uid: int, tier: str, billing_cycle: str) -> UserSubscription:
    async with async_db_session.begin() as db:
        return await subscription_contract_service.activate_from_order(db, order=_Order(user_id=uid, tier=tier, billing_cycle=billing_cycle))


async def _events(uid: int) -> list[CreditGrantEvent]:
    async with async_db_session() as db:
        result = await db.execute(
            select(CreditGrantEvent).where(CreditGrantEvent.user_id == uid).order_by(CreditGrantEvent.id.asc())
        )
        return list(result.scalars().all())


async def _reload(contract_id: int) -> UserSubscription:
    async with async_db_session() as db:
        return (
            await db.execute(select(UserSubscription).where(UserSubscription.id == contract_id))
        ).scalar_one()


async def test_monthly_contract_is_exactly_one_thirty_day_cycle(user_id) -> None:
    """月付合同 = 1 个 30 天周期。用自然月会让 2 月与 8 月的用户拿到不同长度的服务。"""
    contract = await _activate(user_id, 'pro', 'monthly')
    assert contract.cycle_seconds == CYCLE_SECONDS
    assert contract.cycle_count == 1
    assert contract.contract_start_at is not None
    assert contract.contract_end_at is not None
    assert contract.contract_end_at - contract.contract_start_at == timedelta(seconds=CYCLE_SECONDS)
    assert contract.plan_snapshot is not None
    assert contract.plan_snapshot['storage_bytes'] == _PRO_STORAGE_BYTES

    events = await _events(user_id)
    assert len(events) == 1
    payload = events[0].payload
    assert payload['cycle_seconds'] == CYCLE_SECONDS
    assert payload['cycle_count'] == 1
    assert events[0].credit_amount is not None


async def test_yearly_contract_is_twelve_thirty_day_cycles(user_id) -> None:
    """年付合同 = 12 个连续 30 天周期 = 360 天（不是 365 天自然年）。"""
    contract = await _activate(user_id, 'pro', 'yearly')
    assert contract.cycle_count == 12
    assert contract.contract_start_at is not None
    assert contract.contract_end_at is not None
    assert contract.contract_end_at - contract.contract_start_at == timedelta(seconds=CYCLE_SECONDS * 12)
    assert (contract.contract_end_at - contract.contract_start_at).days == 360


async def test_same_tier_renewal_is_scheduled_after_current_contract(user_id) -> None:
    """同档续费：新合同排在旧合同之后（scheduled），不得提前消费。"""
    first = await _activate(user_id, 'pro', 'monthly')
    second = await _activate(user_id, 'pro', 'monthly')

    assert second.status == 'scheduled'
    assert second.contract_start_at == first.contract_end_at
    # 旧合同照常生效，不被提前终止
    assert (await _reload(first.id)).status == 'active'


async def test_upgrade_terminates_current_contract_immediately(user_id) -> None:
    """升级：新合同立即生效，旧合同当场终止并登记到期命令（剩余额度由 NewAPI 清零）。"""
    low = await _activate(user_id, 'pro', 'monthly')
    high = await _activate(user_id, 'flagship', 'monthly')

    assert high.status == 'active'
    assert (await _reload(low.id)).status == 'expired'

    expire_events = [e for e in await _events(user_id) if e.event_type == EVENT_SUBSCRIPTION_EXPIRE]
    assert len(expire_events) == 1
    assert expire_events[0].payload['reason'] == 'upgrade_supersede'
    assert expire_events[0].contract_no == low.contract_no


async def test_downgrade_takes_effect_next_cycle(user_id) -> None:
    """降级下周期生效：立即降级会砍掉用户已经付过钱的这一期额度。"""
    high = await _activate(user_id, 'flagship', 'monthly')
    low = await _activate(user_id, 'pro', 'monthly')

    assert low.status == 'scheduled'
    assert low.contract_start_at == high.contract_end_at
    assert (await _reload(high.id)).status == 'active', '降级不得提前终止当前合同'
    assert not [e for e in await _events(user_id) if e.event_type == EVENT_SUBSCRIPTION_EXPIRE]


async def test_cancel_auto_renew_keeps_quota_until_period_end(user_id) -> None:
    """取消自动续费只改状态，不提前清空额度，也不产生到期命令。"""
    contract = await _activate(user_id, 'pro', 'monthly')
    async with async_db_session.begin() as db:
        changed = await subscription_contract_service.cancel_auto_renew(db, user_id=user_id, app_code=_APP_CODE)
    assert changed is True

    reloaded = await _reload(contract.id)
    assert reloaded.status == STATUS_CANCEL_AT_PERIOD_END
    assert reloaded.auto_renew is False
    assert reloaded.contract_end_at == contract.contract_end_at, '到期时间不得被提前'
    assert not [e for e in await _events(user_id) if e.event_type == EVENT_SUBSCRIPTION_EXPIRE], (
        '取消续费不得触发额度回收——用户已经付过这一期的钱'
    )


async def test_cancelled_contract_still_counts_as_current(user_id) -> None:
    """已取消续费但仍在有效期内的合同仍算「当前合同」。

    否则用户取消续费后再买一份，会同时持有两个可用订阅池。
    """
    await _activate(user_id, 'pro', 'monthly')
    async with async_db_session.begin() as db:
        await subscription_contract_service.cancel_auto_renew(db, user_id=user_id, app_code=_APP_CODE)

    renewed = await _activate(user_id, 'pro', 'monthly')
    assert renewed.status == 'scheduled', '仍在有效期的已取消合同必须被当成当前合同'


async def test_activate_event_payload_matches_contract(user_id) -> None:
    """履约命令的周期参数必须与合同一致——两处写不一样，NewAPI 侧的清零时刻就会漂移。"""
    contract = await _activate(user_id, 'flagship', 'yearly')
    event = [e for e in await _events(user_id) if e.event_type == EVENT_SUBSCRIPTION_ACTIVATE][0]
    assert event.payload['external_subscription_id'] == contract.external_subscription_id
    assert event.payload['cycle_seconds'] == contract.cycle_seconds
    assert event.payload['cycle_count'] == contract.cycle_count
    assert event.contract_no == contract.contract_no
    assert event.subscription_id == contract.id
