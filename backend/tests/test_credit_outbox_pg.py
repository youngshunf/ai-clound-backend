"""doc94 C2 履约 outbox 真实 PostgreSQL 验收。

覆盖清单要求的四种故障注入，外加超时对账的三态语义：

1. **重复支付通知**：同一订单回调 10 次，只留下一条命令、只到账一次；
2. **NewAPI 成功但响应丢失**：超时后用同一个 event_id GET 到 succeeded，收敛而不重复发放；
3. **NewAPI 终局失败**：GET 返回 200+failed → 直接进 dead letter，不再重投；
4. **NewAPI 瞬时失败**：GET 返回 404（确定没发生）→ 用同一个 ID 重投并最终成功；
5. **worker 并发**：两个 worker 同时抢占，靠 FOR UPDATE SKIP LOCKED 各取各的，不重复投递。

NewAPI 侧用**桩客户端**替换出站 HTTP（本用例验证的是云端 outbox 的状态机与幂等，
NewAPI 自身的幂等与精度在 new-api 仓的 Go 用例里对真实数据库验证）。
数据库全程真实 PostgreSQL，零 mock。
"""

from __future__ import annotations

import pathlib
import uuid

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from sqlalchemy import select, text

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.service.credit_grant_event_service import (
    EVENT_WALLET_GRANT,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_RETRYING,
    STATUS_SUCCEEDED,
    credit_grant_event_service,
)
from backend.app.newapi.credit_client import CreditOperationOutcome, NewApiCreditError
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_CREDIT_EVENT_SQL = _BACKEND / 'sql' / 'billing' / 'credit_grant_event.sql'
_CONTRACT_MIGRATION = _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-25-credit-authority-contract-and-outbox.sql'
_TEST_KEY_PREFIX = 'test:doc94:outbox:'


def _outcome(event_id: str, *, status: str = 'succeeded', failure_code: str | None = None) -> CreditOperationOutcome:
    return CreditOperationOutcome(
        event_id=event_id,
        operation_type=EVENT_WALLET_GRANT,
        status=status,
        failure_code=failure_code,
        applied_credits='12.5' if status == 'succeeded' else None,
        idempotent_replay=False,
        completed_at='2026-07-25T00:00:00Z',
        account=None,
        raw={'event_id': event_id, 'status': status},
    )


class StubCreditClient:
    """可编排的 NewAPI 内部通道桩：按脚本决定 PUT/GET 的行为。"""

    def __init__(self) -> None:
        self.put_calls: list[str] = []
        self.get_calls: list[str] = []
        self.put_behaviour: Any = 'succeed'
        self.get_behaviour: Any = 'not_found'

    async def put_credit_operation(self, event_id: str, payload: dict) -> CreditOperationOutcome:
        self.put_calls.append(event_id)
        behaviour = self.put_behaviour
        if callable(behaviour):
            behaviour = behaviour(len(self.put_calls))
        if behaviour == 'succeed':
            return _outcome(event_id)
        if behaviour == 'timeout':
            raise NewApiCreditError('模拟网络超时', code='newapi_credit_unreachable', retryable=True)
        if behaviour == 'terminal':
            raise NewApiCreditError('模拟终局失败', code='invalid_credit_amount', status_code=400, retryable=False)
        raise AssertionError(f'未知 put 行为: {behaviour}')

    async def get_credit_operation(self, event_id: str) -> CreditOperationOutcome | None:
        self.get_calls.append(event_id)
        behaviour = self.get_behaviour
        if callable(behaviour):
            behaviour = behaviour(len(self.get_calls))
        if behaviour == 'not_found':
            return None
        if behaviour == 'succeeded':
            return _outcome(event_id)
        if behaviour == 'failed':
            return _outcome(event_id, status='failed', failure_code='wallet_credit_insufficient')
        raise AssertionError(f'未知 get 行为: {behaviour}')


async def _apply_sql(conn, path: pathlib.Path) -> None:
    raw = path.read_text(encoding='utf-8')
    for stmt in (s.strip() for s in raw.split(';')):
        body = '\n'.join(ln for ln in stmt.splitlines() if not ln.lstrip().startswith('--'))
        if body.strip():
            await conn.exec_driver_sql(body)


@pytest_asyncio.fixture
async def stub(monkeypatch) -> AsyncIterator[StubCreditClient]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

    # 进程级连接池的连接绑定在创建它的事件循环上；pytest-asyncio 每个用例一个新循环，
    # 复用旧池的连接会抛「attached to a different loop」。先 dispose 让池在当前循环重建。
    await async_engine.dispose()

    ddl_engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with ddl_engine.begin() as conn:
            await _apply_sql(conn, _CREDIT_EVENT_SQL)
            await _apply_sql(conn, _CONTRACT_MIGRATION)
    except Exception as exc:
        await ddl_engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    await ddl_engine.dispose()

    client = StubCreditClient()
    import backend.app.billing.service.credit_outbox_service as outbox_module

    monkeypatch.setattr(outbox_module, 'newapi_credit_client', client)
    try:
        yield client
    finally:
        async with async_db_session.begin() as db:
            await db.execute(
                text('DELETE FROM hasn_billing.credit_grant_event WHERE idempotency_key LIKE :p'),
                {'p': f'{_TEST_KEY_PREFIX}%'},
            )
        await async_engine.dispose()


async def _enqueue(key_suffix: str) -> CreditGrantEvent:
    async with async_db_session.begin() as db:
        event = await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_WALLET_GRANT,
            idempotency_key=f'{_TEST_KEY_PREFIX}{key_suffix}',
            user_id=910001,
            newapi_user_id=710001,
            credit_amount=Decimal('12.5'),
            order_no=f'HXTEST{uuid.uuid4().hex[:10].upper()}',
        )
        return event


async def _reload(event_id: str) -> CreditGrantEvent:
    async with async_db_session() as db:
        result = await db.execute(select(CreditGrantEvent).where(CreditGrantEvent.event_id == event_id))
        return result.scalar_one()


async def test_repeated_notifications_leave_exactly_one_command(stub) -> None:
    """重复支付通知 10 次只留下一条命令——幂等键是唯一的防线。"""
    order_no = f'HXTEST{uuid.uuid4().hex[:10].upper()}'
    key = f'{_TEST_KEY_PREFIX}payment:{order_no}'
    for _ in range(10):
        async with async_db_session.begin() as db:
            await credit_grant_event_service.enqueue(
                db,
                event_type=EVENT_WALLET_GRANT,
                idempotency_key=key,
                user_id=910002,
                newapi_user_id=710002,
                credit_amount=Decimal('30'),
                order_no=order_no,
            )
    async with async_db_session() as db:
        count = await db.execute(
            text('SELECT count(*) FROM hasn_billing.credit_grant_event WHERE idempotency_key = :k'), {'k': key}
        )
        assert count.scalar() == 1


async def test_successful_delivery_records_applied_credits_from_receipt(stub) -> None:
    """成功后以 NewAPI 回执的 applied_credits 入审计，不以请求值反推。"""
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    event = await _enqueue('deliver-ok')
    assert event.status == STATUS_PENDING

    summary = await credit_outbox_service.run_once()
    assert summary['succeeded'] >= 1

    reloaded = await _reload(event.event_id)
    assert reloaded.status == STATUS_SUCCEEDED
    assert reloaded.applied_credits == Decimal('12.5')
    assert reloaded.delivered_at is not None


async def test_timeout_then_get_succeeded_converges_without_reissuing(stub) -> None:
    """NewAPI 成功但响应丢失：同 event_id GET 到 succeeded → 收敛，不重复到账。"""
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    event = await _enqueue('timeout-succeeded')
    stub.put_behaviour = 'timeout'
    stub.get_behaviour = 'succeeded'

    await credit_outbox_service.run_once()

    reloaded = await _reload(event.event_id)
    assert reloaded.status == STATUS_SUCCEEDED
    assert stub.put_calls == [event.event_id], '超时后只能用同一个 event_id 对账，禁止换 ID 重发'
    assert stub.get_calls == [event.event_id]


async def test_timeout_then_get_failed_goes_dead(stub) -> None:
    """GET 返回 200+failed = 已终局失败 → 直接进 dead letter，不再重投。"""
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    event = await _enqueue('timeout-failed')
    stub.put_behaviour = 'timeout'
    stub.get_behaviour = 'failed'

    await credit_outbox_service.run_once()

    reloaded = await _reload(event.event_id)
    assert reloaded.status == STATUS_DEAD
    assert reloaded.last_error_code == 'wallet_credit_insufficient'


async def test_timeout_then_get_404_retries_with_same_event_id(stub) -> None:
    """GET 404 = 这次操作确定没发生 → 用同一个 event_id 重投并最终成功。"""
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    event = await _enqueue('timeout-notfound')
    stub.put_behaviour = lambda attempt: 'timeout' if attempt == 1 else 'succeed'
    stub.get_behaviour = 'not_found'

    await credit_outbox_service.run_once()
    first = await _reload(event.event_id)
    assert first.status == STATUS_RETRYING
    assert first.attempt_count == 1

    # 让重试立刻到点，模拟退避结束
    async with async_db_session.begin() as db:
        await db.execute(
            text('UPDATE hasn_billing.credit_grant_event SET next_attempt_at = NOW() WHERE event_id = :e'),
            {'e': event.event_id},
        )
    await credit_outbox_service.run_once()

    final = await _reload(event.event_id)
    assert final.status == STATUS_SUCCEEDED
    assert stub.put_calls == [event.event_id, event.event_id], '重投必须复用同一个 event_id'


async def test_terminal_4xx_goes_dead_without_retry(stub) -> None:
    """终局 4xx 直接进 dead letter，不做无意义重试。"""
    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    event = await _enqueue('terminal-4xx')
    stub.put_behaviour = 'terminal'

    await credit_outbox_service.run_once()

    reloaded = await _reload(event.event_id)
    assert reloaded.status == STATUS_DEAD
    assert reloaded.last_error_code == 'invalid_credit_amount'
    assert stub.get_calls == [], '终局失败不需要再对账'


async def test_concurrent_workers_do_not_double_deliver(stub) -> None:
    """两个 worker 并发抢占：SKIP LOCKED 让它们各取各的，同一事件只投递一次。"""
    import asyncio

    from backend.app.billing.service.credit_outbox_service import credit_outbox_service

    events = [await _enqueue(f'concurrent-{i}') for i in range(6)]
    await asyncio.gather(credit_outbox_service.run_once(), credit_outbox_service.run_once())

    for event in events:
        reloaded = await _reload(event.event_id)
        assert reloaded.status == STATUS_SUCCEEDED
        assert reloaded.attempt_count == 1, '同一事件被抢占两次说明 SKIP LOCKED 没生效'
    assert sorted(stub.put_calls) == sorted(e.event_id for e in events)
