"""doc94 C1 数据层真实 PostgreSQL 验收（零 mock）。

覆盖四件事：
1. 两份 SQL 可重复执行（幂等），迁移可以安全重跑；
2. 履约事件的两条唯一约束真实生效——event_id 是 NewAPI 侧幂等资源 ID，
   idempotency_key 保证同一业务动作只留一条命令；
3. 支付状态与履约状态可独立表达，且订单默认不处于「待履约」假状态；
4. 合同表新增字段全部落地，且**没有**新增任何余额/已用量/累计用量字段——
   云端已彻底退出积分余额，任何一列都会把「双权威」放回来。

需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import pathlib
import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_CREDIT_EVENT_SQL = _BACKEND / 'sql' / 'billing' / 'credit_grant_event.sql'
_CONTRACT_MIGRATION = _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-25-credit-authority-contract-and-outbox.sql'

# 云端不得再出现的余额/用量列名：出现任一即说明「双权威」被放了回来。
_FORBIDDEN_BALANCE_COLUMNS = {
    'remaining_credits',
    'available_credits',
    'balance_credits',
    'credit_balance',
    'used_quota',
    'total_used_credits',
    'cached_credits',
}


async def _apply_sql(conn, path: pathlib.Path) -> None:
    raw = path.read_text(encoding='utf-8')
    for stmt in (s.strip() for s in raw.split(';')):
        body = '\n'.join(ln for ln in stmt.splitlines() if not ln.lstrip().startswith('--'))
        if body.strip():
            await conn.exec_driver_sql(body)


@pytest_asyncio.fixture
async def db() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    # 连续应用两遍：迁移必须幂等，重跑不得报错。
    for _ in range(2):
        async with engine.begin() as conn:
            await _apply_sql(conn, _CREDIT_EVENT_SQL)
            await _apply_sql(conn, _CONTRACT_MIGRATION)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.execute(
            text("DELETE FROM hasn_billing.credit_grant_event WHERE idempotency_key LIKE 'test:doc94:%'")
        )
        await session.execute(text("DELETE FROM hasn_billing.user_subscription WHERE app_code = 'doc94test'"))
        await session.commit()
        await session.close()
        await engine.dispose()


async def _insert_event(session, *, idempotency_key: str, event_id: str | None = None) -> None:
    await session.execute(
        text("""
            INSERT INTO hasn_billing.credit_grant_event
                (event_id, idempotency_key, event_type, user_id, newapi_user_id, credit_amount)
            VALUES (:event_id, :key, 'wallet_grant', 900001, 700001, 12.34567)
        """),
        {'event_id': event_id or str(uuid.uuid4()), 'key': idempotency_key},
    )


async def test_idempotency_key_is_unique(db) -> None:
    """同一业务动作重复触发只能留下一条命令，否则会重复发积分。"""
    key = 'test:doc94:payment:ORDER0001:wallet'
    await _insert_event(db, idempotency_key=key)
    await db.commit()

    with pytest.raises(IntegrityError):
        await _insert_event(db, idempotency_key=key)
        await db.commit()
    await db.rollback()


async def test_event_id_is_unique(db) -> None:
    """event_id 是 NewAPI 幂等履约的资源 ID，两条事件绝不能撞同一个。"""
    shared = str(uuid.uuid4())
    await _insert_event(db, idempotency_key='test:doc94:evt:a', event_id=shared)
    await db.commit()

    with pytest.raises(IntegrityError):
        await _insert_event(db, idempotency_key='test:doc94:evt:b', event_id=shared)
        await db.commit()
    await db.rollback()


async def test_credit_amount_keeps_five_decimals(db) -> None:
    """金额列必须是 NUMERIC(18,5)：6 位小数不可被 NewAPI 精确表示，多存一位只会制造对不上的审计。"""
    result = await db.execute(
        text("""
            SELECT numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'hasn_billing'
              AND table_name = 'credit_grant_event'
              AND column_name IN ('credit_amount', 'applied_credits')
        """)
    )
    scales = result.all()
    assert len(scales) == 2
    for precision, scale in scales:
        assert (precision, scale) == (18, 5)


async def test_payment_and_fulfillment_status_are_independent(db) -> None:
    """订单默认不处于「待履约」：未支付的订单不该看起来像在发货。"""
    result = await db.execute(
        text("""
            SELECT column_name, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'hasn_billing'
              AND table_name = 'pay_order'
              AND column_name IN ('fulfillment_status', 'fulfilled_at', 'fulfillment_error_code', 'fulfillment_event_id')
        """)
    )
    columns = {row[0]: row for row in result.all()}
    assert set(columns) == {'fulfillment_status', 'fulfilled_at', 'fulfillment_error_code', 'fulfillment_event_id'}
    assert "'not_required'" in (columns['fulfillment_status'][1] or '')
    assert columns['fulfillment_status'][2] == 'NO'


async def test_only_one_active_contract_per_user(db) -> None:
    """同用户同应用只允许一份 active 合同：升级/续费必须显式处理旧合同，不能靠再插一行绕过。"""
    insert = text("""
        INSERT INTO hasn_billing.user_subscription
            (app_code, user_id, tier, subscription_type, status, contract_no, cycle_seconds, cycle_count,
             monthly_credits, current_credits, used_credits, purchased_credits,
             billing_cycle_start, billing_cycle_end, auto_renew, max_agents)
        VALUES ('doc94test', :uid, 'pro', 'monthly', :status, :contract_no, 2592000, 1,
                0, 0, 0, 0, NOW(), NOW(), true, 1)
    """)
    await db.execute(insert, {'uid': 900002, 'status': 'active', 'contract_no': 'test-doc94-c1'})
    await db.commit()

    with pytest.raises(IntegrityError):
        await db.execute(insert, {'uid': 900002, 'status': 'active', 'contract_no': 'test-doc94-c2'})
        await db.commit()
    await db.rollback()

    # 一份未来合同（scheduled）与当前 active 可以并存——这是提前续费的正常形态。
    await db.execute(insert, {'uid': 900002, 'status': 'scheduled', 'contract_no': 'test-doc94-c3'})
    await db.commit()


async def test_contract_columns_landed_without_any_balance_column(db) -> None:
    """合同字段全部落地，且云端没有新增任何余额/已用量字段。"""
    result = await db.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'hasn_billing'
              AND table_name = 'user_subscription'
        """)
    )
    columns = {row[0] for row in result.all()}

    expected = {
        'contract_no',
        'offering_key',
        'plan_key',
        'contract_start_at',
        'contract_end_at',
        'cycle_seconds',
        'cycle_count',
        'plan_snapshot',
        'source_order_no',
        'external_subscription_id',
        'fulfillment_status',
        'free_policy_version',
        'free_grant_epoch',
    }
    assert expected <= columns

    assert not (columns & _FORBIDDEN_BALANCE_COLUMNS), '云端不得新增任何余额/用量列，余额只有 NewAPI 一个权威'


async def test_cycle_seconds_is_thirty_days_for_every_contract(db) -> None:
    """周期口径恒为 30 天（含免费档），绝不留自然月。"""
    result = await db.execute(
        text("""
            SELECT column_default, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'hasn_billing'
              AND table_name = 'user_subscription'
              AND column_name = 'cycle_seconds'
        """)
    )
    default, nullable = result.one()
    assert '2592000' in (default or '')
    assert nullable == 'NO'

    mismatched = await db.execute(text('SELECT count(*) FROM hasn_billing.user_subscription WHERE cycle_seconds <> 2592000'))
    assert mismatched.scalar() == 0
