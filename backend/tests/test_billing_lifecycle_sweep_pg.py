"""MK-5 统一商业化生命周期 sweeper·真实 PG 验收（实施/92 MK-5）——零 mock。

覆盖「到期状态机：提醒(7/3/1天·去重)→到期→变更事件」：
1. _days_until 纯函数在 7/3/1 边界的取整（提醒阈值命中）；
2. 到期：已过期的 active 权益/付费订阅被置 expired（收编两支旧 sweeper 动作）；
3. 提醒去重：同一 (billing_kind, ref, 阈值) 多轮 sweep 只落一条未读通知（dedupe_key 聚合）；
4. 免费版（subscription_end_date=NULL）永不过期、不参与；未到期权益不被误伤。

需本地 PostgreSQL :15432（含 hasn_billing.user_subscription / hasn_app_entitlement /
hasn_humans / hasn_notifications）。sweeper 内部用全局 async_db_session（生产真实行为），
故 teardown dispose 全局 engine 让下个测试的事件循环重建连接。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing import tasks as billing_tasks
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_OWNER = 'h_mk5_sweep_test'
_USER_ID = 990041  # 测试专用 user_id，避开真实用户
_APP_ID = 'app:mk5_sweep_test'


# ── 1. 纯函数：到期天数取整命中 7/3/1 阈值 ──
def test_days_until_rounds_up_to_reminder_thresholds() -> None:
    now = timezone.now()
    # 剩余不足整天向上取整；到期阈值集合 {7,3,1} 逐日各命中一次
    assert billing_tasks._days_until(now + timedelta(days=6, hours=12), now) == 7
    assert billing_tasks._days_until(now + timedelta(days=2, hours=12), now) == 3
    assert billing_tasks._days_until(now + timedelta(hours=12), now) == 1
    assert billing_tasks._days_until(now - timedelta(hours=1), now) == 0  # 已过期


# ── 真实 PG fixture（NullPool 自持 + teardown dispose 全局 engine）──
@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()

    async def _purge() -> None:
        await s.execute(delete(HasnNotifications).where(HasnNotifications.target_id == _OWNER))
        await s.execute(delete(HasnAppEntitlement).where(HasnAppEntitlement.subject_id == _OWNER))
        await s.execute(delete(UserSubscription).where(UserSubscription.user_id == _USER_ID))
        await s.execute(delete(HasnHumans).where(HasnHumans.hasn_id == _OWNER))
        await s.commit()

    try:
        await _purge()
        # owner ↔ user_id 映射（订阅按 user_id 归属·通知/WSPUSH 按 hasn_id）
        # star_id 非空且唯一（避开 idx_hasn_humans_star_id 与其它行 '' 撞键）
        s.add(
            HasnHumans(
                hasn_id=_OWNER, star_id='mk5sweep990041', user_id=_USER_ID, nickname='MK5 扫测主人', status='active'
            )
        )
        await s.commit()
        yield s
    finally:
        await _purge()
        await s.close()
        await engine.dispose()
        # sweeper 内部走全局 async_db_session；pytest-asyncio 每测试独立事件循环，须 dispose 回收
        await async_engine.dispose()


def _mk_subscription(
    *, tier: str, end_delta: timedelta | None, status: str = 'active', app_code: str = 'huanxing'
) -> UserSubscription:
    # 同一 user 每 app_code 仅一条订阅（uq_user_subscription_user_app）；多档需借不同 app_code
    return UserSubscription(
        app_code=app_code,
        user_id=_USER_ID,
        tier=tier,
        subscription_type='monthly',
        monthly_credits=Decimal(0),
        current_credits=Decimal(0),
        used_credits=Decimal(0),
        purchased_credits=Decimal(0),
        status=status,
        max_agents=1,
        subscription_end_date=(timezone.now() + end_delta) if end_delta is not None else None,
    )


def _mk_entitlement(
    *, expires_delta: timedelta | None, status: str = 'active', app_id: str = _APP_ID
) -> HasnAppEntitlement:
    # 同一 (app_id, subject_type, subject_id) 仅一条 active（uq_app_entitlement_active 偏索引）；
    # 同主人要多条 active 权益须借不同 app_id。
    return HasnAppEntitlement(
        app_id=app_id,
        subject_type='owner',
        subject_id=_OWNER,
        source='purchase',
        status=status,
        feature_key=app_id,
        quota_json={},
        expires_at=(timezone.now() + expires_delta) if expires_delta is not None else None,
    )


async def test_expiry_marks_overdue_active_as_expired(sess: AsyncSession) -> None:
    """已过期的 active 付费订阅 + 权益被 sweep 置 expired；免费版与未到期权益不动。"""
    paid = _mk_subscription(tier='pro', end_delta=timedelta(days=-1))  # 昨天到期·付费·仍 active
    free = _mk_subscription(
        tier='free', end_delta=None, app_code='mk5free_test'
    )  # 免费·永不过期（借 app_code 避唯一键）
    overdue_ent = _mk_entitlement(expires_delta=timedelta(days=-1))  # 昨天到期·仍 active
    # 借不同 app_id 让两条 active 权益共存（避 uq_app_entitlement_active 偏索引撞键）
    future_ent = _mk_entitlement(expires_delta=timedelta(days=30), app_id=f'{_APP_ID}_future')  # 30 天后到期·不动
    sess.add_all([paid, free, overdue_ent, future_ent])
    await sess.commit()
    for r in (paid, free, overdue_ent, future_ent):
        await sess.refresh(r)

    result = await billing_tasks.run_billing_lifecycle_sweep()

    # 断言我方行的权威态（行级·不依赖全局计数，避开 dev 库其它行干扰）
    for r in (paid, free, overdue_ent, future_ent):
        await sess.refresh(r)
    assert paid.status == 'expired', '过期付费订阅应置 expired'
    assert free.status == 'active', '免费版永不过期'
    assert overdue_ent.status == 'expired', '过期权益应置 expired'
    assert future_ent.status == 'active', '未到期权益不被误伤'
    # 计数至少覆盖我方各一行；owner 变更事件已触发（best-effort WSPUSH）
    assert result['expired_sub'] >= 1
    assert result['expired_ent'] >= 1
    assert result['affected_owners'] >= 1


async def test_expiry_reminder_dedup_across_runs(sess: AsyncSession) -> None:
    """到期前 3 天：多轮 sweep 只落一条未读提醒（dedupe_key 聚合·不重复轰炸）。"""
    # 2.5 天后到期 → _days_until 向上取整 = 3 → 命中提醒阈值
    sub = _mk_subscription(tier='pro', end_delta=timedelta(days=2, hours=12))
    sess.add(sub)
    await sess.commit()
    await sess.refresh(sub)
    dedupe_key = f'billing_expiry:subscription:{sub.id}:3'

    # 连跑两轮（模拟连续两天/misfire 重跑）
    await billing_tasks.run_billing_lifecycle_sweep()
    await billing_tasks.run_billing_lifecycle_sweep()

    rows = (
        (
            await sess.execute(
                select(HasnNotifications).where(
                    HasnNotifications.target_id == _OWNER,
                    HasnNotifications.dedupe_key == dedupe_key,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, '同阈值多轮 sweep 只应落一条未读通知（去重聚合）'
    row = rows[0]
    assert row.category == 'commerce'
    assert row.type == 'billing_expiry'
    # 聚合计数递增（第二轮命中未读行 → aggregated_count=2），未重复落行
    assert int((row.data or {}).get('aggregated_count', 1)) == 2
    # 未到期（仍剩 ~2.5 天）→ 订阅本身不被置 expired
    await sess.refresh(sub)
    assert sub.status == 'active'


async def test_far_future_no_reminder_no_expiry(sess: AsyncSession) -> None:
    """远未到期（>7 天且非 7/3/1）→ 既不提醒也不过期。"""
    sub = _mk_subscription(tier='pro', end_delta=timedelta(days=40))
    sess.add(sub)
    await sess.commit()
    await sess.refresh(sub)

    await billing_tasks.run_billing_lifecycle_sweep()

    reminders = (
        (
            await sess.execute(
                select(HasnNotifications).where(
                    HasnNotifications.target_id == _OWNER,
                    HasnNotifications.type == 'billing_expiry',
                )
            )
        )
        .scalars()
        .all()
    )
    assert reminders == [], '远未到期不应产生提醒'
    await sess.refresh(sub)
    assert sub.status == 'active'
