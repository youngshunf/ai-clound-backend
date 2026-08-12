"""doc94 F1 非支付授予路径真实 PostgreSQL 验收。

这三条路径过去都直接改云端余额，是「双权威」的另外三个入口。本用例锁住的是
**幂等键的三处要害**——写错任何一处，结果都是「该发的发不出去」或「不该发的发两次」：

1. 免费档撤销后重新授予：``epoch`` 递增，新命令不被旧幂等键挡住，额度真实到账；
2. 管理员连续两笔赠送：两笔都到账（键含独立单据号，第二笔不被当重放吞掉）；
3. 注册奖励：同活动同版本对同一用户只发一次，改版本才能重发。

需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import pathlib
import uuid

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service.credit_grant_event_service import (
    EVENT_SUBSCRIPTION_ACTIVATE,
    EVENT_WALLET_GRANT,
    CYCLE_SECONDS,
)
from backend.app.billing.service.credit_grant_service import credit_grant_service
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
_APP_CODE = 'doc94f1'


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

    uid = 950_000_000 + int(uuid.uuid4().hex[:6], 16) % 1_000_000

    # NewAPI 用户映射由 `_resolve_newapi_user_id` 解析；这里桩掉它，
    # 本用例验证的是幂等键与合同口径，不是映射查询本身。
    async def _fake_resolve(db, user, app_code):  # noqa: ANN001
        return 700_000 + (user % 1000)

    import backend.app.billing.service.pay_callbacks as callbacks_module

    monkeypatch.setattr(callbacks_module, '_resolve_newapi_user_id', _fake_resolve)

    # 免费档配置：doc94 D1 起 ensure_free_contract 从商品目录取每周期额度。
    seed = CatalogSeed()
    async with async_db_session.begin() as db:
        await seed.seed_tier(db, tier_name='free', credits_per_cycle=100, monthly_price=0, yearly_price=None, max_agents=1)
    try:
        yield uid
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': uid})
            await db.execute(text('DELETE FROM hasn_billing.user_subscription WHERE app_code = :a'), {'a': _APP_CODE})
            await seed.restore(db)
        await async_engine.dispose()


async def _events(uid: int) -> list[CreditGrantEvent]:
    async with async_db_session() as db:
        result = await db.execute(
            select(CreditGrantEvent).where(CreditGrantEvent.user_id == uid).order_by(CreditGrantEvent.id.asc())
        )
        return list(result.scalars().all())


async def test_free_contract_uses_thirty_day_cycle_without_commercial_expiry(user_id) -> None:
    """免费合同固化周期与存储权益，且不用「100 年到期」伪造永久合同。"""
    async with async_db_session.begin() as db:
        contract = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    assert contract is not None
    assert contract.cycle_seconds == CYCLE_SECONDS
    assert contract.cycle_count is None
    assert contract.contract_end_at is None
    assert contract.plan_snapshot is not None
    assert contract.plan_snapshot['storage_bytes'] == 100 * 1024**3

    events = await _events(user_id)
    assert len(events) == 1
    assert events[0].event_type == EVENT_SUBSCRIPTION_ACTIVATE
    assert events[0].idempotency_key.endswith(':activate')
    assert events[0].payload['end_at'] is None
    assert events[0].payload['cycle_count'] is None


async def test_free_contract_does_not_rewrite_zero_max_agents(user_id) -> None:
    """目录异常值 0 必须原样进入合同，不能被 ``or 1`` 静默篡改并掩盖配置故障。"""
    seed = CatalogSeed()
    async with async_db_session.begin() as db:
        await seed.seed_tier(
            db,
            tier_name='free',
            credits_per_cycle=100,
            monthly_price=0,
            yearly_price=None,
            max_agents=0,
        )
    try:
        async with async_db_session.begin() as db:
            contract = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
        assert contract is not None
        assert contract.max_agents == 0
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': user_id})
            await db.execute(
                text('DELETE FROM hasn_billing.user_subscription WHERE app_code = :a AND user_id = :u'),
                {'a': _APP_CODE, 'u': user_id},
            )
            await seed.restore(db)


async def test_free_grant_after_revocation_is_not_blocked_by_old_key(user_id) -> None:
    """免费额度撤销后重新授予必须真实到账。

    若幂等键只是 ``free:{user_id}:activate``，撤销后再授予会被自己写下的键永久挡住——
    该用户此生再也发不出第二次免费额度。epoch 就是为了防住这条。
    """
    async with async_db_session.begin() as db:
        first = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    assert first is not None
    async with async_db_session.begin() as db:
        await credit_grant_service.revoke_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    async with async_db_session.begin() as db:
        second = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    assert second is not None and second.id != first.id
    assert second.free_grant_epoch == first.free_grant_epoch + 1

    activate_keys = [e.idempotency_key for e in await _events(user_id) if e.event_type == EVENT_SUBSCRIPTION_ACTIVATE]
    assert len(activate_keys) == 2, '撤销后重授必须产生一条新的授予命令'
    assert len(set(activate_keys)) == 2, '两次授予的幂等键必须不同，否则第二次会被当重放吞掉'


async def test_two_admin_grants_both_land(user_id) -> None:
    """管理员连续两笔赠送都必须到账：键以单据号为组件，不是 user_id。"""
    async with async_db_session.begin() as db:
        first = await credit_grant_service.admin_grant(db, user_id=user_id, credits=Decimal('10'), app_code=_APP_CODE)
    async with async_db_session.begin() as db:
        second = await credit_grant_service.admin_grant(db, user_id=user_id, credits=Decimal('20'), app_code=_APP_CODE)

    assert first.idempotency_key != second.idempotency_key
    assert first.event_id != second.event_id
    grants = [e for e in await _events(user_id) if e.event_type == EVENT_WALLET_GRANT]
    assert len(grants) == 2
    assert sorted(str(e.credit_amount) for e in grants) == ['10.00000', '20.00000']


async def test_same_grant_no_is_idempotent(user_id) -> None:
    """同一张赠送单据重复提交只留下一条命令。"""
    grant_no = f'AGTEST{uuid.uuid4().hex[:10].upper()}'
    async with async_db_session.begin() as db:
        first = await credit_grant_service.admin_grant(
            db, user_id=user_id, credits=Decimal('10'), grant_no=grant_no, app_code=_APP_CODE
        )
    async with async_db_session.begin() as db:
        second = await credit_grant_service.admin_grant(
            db, user_id=user_id, credits=Decimal('10'), grant_no=grant_no, app_code=_APP_CODE
        )
    assert first.event_id == second.event_id


async def test_registration_bonus_is_once_per_campaign_version(user_id, monkeypatch) -> None:
    """注册奖励：同活动同版本只发一次；递增版本号才能重新发放。"""
    from backend.core.conf import settings

    async with async_db_session.begin() as db:
        first = await credit_grant_service.grant_registration_bonus(db, user_id=user_id, app_code=_APP_CODE)
    async with async_db_session.begin() as db:
        replay = await credit_grant_service.grant_registration_bonus(db, user_id=user_id, app_code=_APP_CODE)
    assert first is not None and replay is not None
    assert first.event_id == replay.event_id, '同活动同版本重复调用必须命中同一条命令'

    monkeypatch.setattr(settings, 'REGISTER_BONUS_CAMPAIGN_VERSION', settings.REGISTER_BONUS_CAMPAIGN_VERSION + 1)
    async with async_db_session.begin() as db:
        after_bump = await credit_grant_service.grant_registration_bonus(db, user_id=user_id, app_code=_APP_CODE)
    assert after_bump is not None
    assert after_bump.event_id != first.event_id, '活动版本递增后必须能重新发放'


async def test_free_contract_is_not_duplicated_while_active(user_id) -> None:
    """已有生效合同时不重复建免费合同。"""
    async with async_db_session.begin() as db:
        first = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    async with async_db_session.begin() as db:
        second = await credit_grant_service.ensure_free_contract(db, user_id=user_id, app_code=_APP_CODE)
    assert first is not None and second is not None and first.id == second.id

    async with async_db_session() as db:
        count = await db.execute(
            select(UserSubscription).where(UserSubscription.app_code == _APP_CODE, UserSubscription.user_id == user_id)
        )
        assert len(list(count.scalars().all())) == 1
