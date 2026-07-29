"""sync inbox 与业务 handler 两事务编排的真实 PostgreSQL 崩溃窗口测试。"""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.sync_business_handlers import (
    MEMORY_SYNC_EVENTS,
    SESSION_SYNC_EVENT,
    MemorySyncHandler,
    SessionSyncHandler,
    build_sync_handler_registry,
)
from backend.app.hasn.service.hasn_sync_business_receipts_service import (
    hasn_sync_business_receipts_service,
)
from backend.app.hasn.sync_inbox_worker import SyncInboxWorker
from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.app.hasn_sync.application.push import accept_envelopes
from backend.app.hasn_sync.ports.dto import InboxEnvelope
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone


pytestmark = pytest.mark.asyncio
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')
_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')


@pytest_asyncio.fixture
async def worker_env():
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        future=True,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
            columns = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_name = 'hasn_sync_inbox_events'
                          AND column_name IN (
                            'attempt_count', 'next_attempt_at', 'locked_by',
                            'locked_at', 'last_error', 'applied_at', 'dead_at'
                          )
                        """
                    )
                )
            ).scalar_one()
            if columns != 7:
                pytest.fail('R3 sync inbox worker migration 尚未应用')
            receipt_table = (
                await conn.execute(
                    sa.text(
                        "SELECT to_regclass('public.hasn_sync_business_receipts')"
                    )
                )
            ).scalar_one()
            if receipt_table is None:
                pytest.fail('sync 业务幂等 receipt 表尚未创建')
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达：{exc}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = f'h_iw{uuid.uuid4().hex[:18]}'
    session_id = f'sess_{uuid.uuid4().hex[:20]}'
    envelope = InboxEnvelope(
        owner_id=owner,
        node_id='node-inbox-worker-pg',
        client_event_id=f'ce_{uuid.uuid4().hex[:20]}',
        hasn_id=owner,
        event_type=SESSION_SYNC_EVENT,
        payload={
            'session_id': session_id,
            'hasn_id': owner,
            'session_kind': 'interactive',
            'session_scope': 'summary_only',
            'session_status': 'active',
            'origin_type': 'api',
            'title': 'R3 inbox worker 真实事务探针',
            'summary_checkpoint_json': {'revision': 1},
        },
        dedupe_key=session_id,
    )
    try:
        yield sessions, envelope
    finally:
        async with sessions.begin() as db:
            await db.execute(
                sa.text(
                    'DELETE FROM public.hasn_sync_business_receipts '
                    'WHERE owner_id = :owner_id'
                ),
                {'owner_id': owner},
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_INBOX} '
                    'WHERE owner_id = :owner_id'
                ),
                {'owner_id': owner},
            )
            await db.execute(
                sa.text(
                    'DELETE FROM public.hasn_sessions '
                    'WHERE session_id = :session_id'
                ),
                {'session_id': session_id},
            )
            await db.execute(
                sa.text(f'DELETE FROM {_EVENTS} WHERE owner_id = :owner_id'),
                {'owner_id': owner},
            )
            await db.execute(
                sa.text(
                    'DELETE FROM hasn_memory.namespace_revision '
                    'WHERE sync_scope_id = :owner_id'
                ),
                {'owner_id': owner},
            )
        await engine.dispose()


async def _accept(sessions, envelope: InboxEnvelope) -> None:
    async with sessions.begin() as db:
        result = await accept_envelopes(db, (envelope,))
    assert result.items[0].status == 'accepted'


async def _session_count(sessions, session_id: str) -> int:
    async with sessions() as db:
        return int(
            (
                await db.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_sessions '
                        'WHERE session_id = :session_id'
                    ),
                    {'session_id': session_id},
                )
            ).scalar_one()
        )


async def _receipt_count(sessions, envelope: InboxEnvelope) -> int:
    async with sessions() as db:
        return int(
            (
                await db.execute(
                    sa.text(
                        'SELECT count(*) '
                        'FROM public.hasn_sync_business_receipts '
                        'WHERE owner_id = :owner_id '
                        'AND node_id = :node_id '
                        'AND client_event_id = :client_event_id'
                    ),
                    {
                        'owner_id': envelope.owner_id,
                        'node_id': envelope.node_id,
                        'client_event_id': envelope.client_event_id,
                    },
                )
            ).scalar_one()
        )


async def _sync_event_count(sessions, envelope: InboxEnvelope) -> int:
    async with sessions() as db:
        return int(
            (
                await db.execute(
                    sa.text(
                        f'SELECT count(*) FROM {_EVENTS} '
                        'WHERE owner_id = :owner_id '
                        'AND event_type = :event_type'
                    ),
                    {
                        'owner_id': envelope.owner_id,
                        'event_type': envelope.event_type,
                    },
                )
            ).scalar_one()
        )


async def _inbox_status(sessions, envelope: InboxEnvelope) -> tuple[str, int]:
    async with sessions() as db:
        row = (
            await db.execute(
                sa.text(
                    f'SELECT status, attempt_count FROM {_INBOX} '
                    'WHERE owner_id = :owner_id '
                    'AND node_id = :node_id '
                    'AND client_event_id = :client_event_id'
                ),
                {
                    'owner_id': envelope.owner_id,
                    'node_id': envelope.node_id,
                    'client_event_id': envelope.client_event_id,
                },
            )
        ).one()
    return str(row.status), int(row.attempt_count)


def _worker(sessions) -> SyncInboxWorker:
    return SyncInboxWorker(
        sync_session_factory=sessions,
        business_session_factory=sessions,
        registry=build_sync_handler_registry(),
        instance_id='inbox-worker-pg',
        lease_seconds=1,
        max_attempts=3,
    )


async def test_crash_after_receive_before_business_apply_is_recoverable(
    worker_env,
) -> None:
    """inbox 已提交、业务尚未开始时进程退出，重启后仍可应用并 ACK。"""
    sessions, envelope = worker_env
    await _accept(sessions, envelope)
    assert await _session_count(sessions, envelope.payload['session_id']) == 0

    processed = await _worker(sessions).process_one()

    assert processed is True
    assert await _session_count(sessions, envelope.payload['session_id']) == 1
    assert await _receipt_count(sessions, envelope) == 1
    assert await _sync_event_count(sessions, envelope) == 1
    assert await _inbox_status(sessions, envelope) == ('applied', 1)


async def test_crash_before_business_commit_rolls_back_then_retries_once(
    worker_env,
) -> None:
    """handler 写入后业务事务回滚，不得伪标 applied；重试后只留一条业务行。"""
    sessions, envelope = worker_env
    await _accept(sessions, envelope)
    store = SQLAlchemySyncStore()
    now = timezone.now()
    async with sessions.begin() as sync_db:
        claimed = await store.claim_inbox(
            sync_db,
            instance_id='crash-before-commit',
            now=now,
            lease_seconds=1,
            event_types=(SESSION_SYNC_EVENT,),
        )
    assert claimed is not None

    handler = SessionSyncHandler()
    async with sessions() as business_db:
        transaction = await business_db.begin()
        await handler.apply(
            business_db,
            claimed.envelope,
            idempotency_key=claimed.idempotency_key,
        )
        await transaction.rollback()
    assert await _session_count(sessions, envelope.payload['session_id']) == 0

    async with sessions.begin() as sync_db:
        await store.mark_inbox_failed(
            sync_db,
            claimed,
            error='测试：业务提交前进程退出',
            now=now,
            max_attempts=3,
        )
        await sync_db.execute(
            sa.text(
                f'UPDATE {_INBOX} SET next_attempt_at = :now '
                'WHERE id = :row_id'
            ),
            {'now': now, 'row_id': claimed.row_id},
        )

    processed = await _worker(sessions).process_one(now=now)
    assert processed is True
    assert await _session_count(sessions, envelope.payload['session_id']) == 1
    assert await _receipt_count(sessions, envelope) == 1
    assert await _sync_event_count(sessions, envelope) == 1
    assert await _inbox_status(sessions, envelope) == ('applied', 2)


async def test_crash_after_business_commit_before_ack_replays_idempotently(
    worker_env,
) -> None:
    """业务已提交但 sync ACK 未提交时，租约过期重放不得重复业务状态。"""
    sessions, envelope = worker_env
    await _accept(sessions, envelope)
    store = SQLAlchemySyncStore()
    now = timezone.now()
    async with sessions.begin() as sync_db:
        claimed = await store.claim_inbox(
            sync_db,
            instance_id='crash-after-commit',
            now=now,
            lease_seconds=1,
            event_types=(SESSION_SYNC_EVENT,),
        )
    assert claimed is not None

    async with sessions.begin() as business_db:
        await SessionSyncHandler().apply(
            business_db,
            claimed.envelope,
            idempotency_key=claimed.idempotency_key,
        )
    assert await _session_count(sessions, envelope.payload['session_id']) == 1

    async with sessions.begin() as sync_db:
        await sync_db.execute(
            sa.text(
                f'UPDATE {_INBOX} SET locked_at = :expired '
                'WHERE id = :row_id'
            ),
            {
                'expired': now - timedelta(seconds=2),
                'row_id': claimed.row_id,
            },
        )

    processed = await _worker(sessions).process_one(now=now)
    assert processed is True
    assert await _session_count(sessions, envelope.payload['session_id']) == 1
    assert await _receipt_count(sessions, envelope) == 1
    assert await _sync_event_count(sessions, envelope) == 1
    assert await _inbox_status(sessions, envelope) == ('applied', 2)


async def test_memory_commit_before_ack_replay_does_not_advance_revision_twice(
    worker_env,
) -> None:
    """receipt 与记忆写同事务提交后，ACK 前崩溃重放不得二次推进 revision。"""
    sessions, base_envelope = worker_env
    event_type = 'memory.owner_event.upserted'
    assert event_type in MEMORY_SYNC_EVENTS
    envelope = InboxEnvelope(
        owner_id=base_envelope.owner_id,
        node_id=base_envelope.node_id,
        client_event_id=f'ce_mem_{uuid.uuid4().hex[:18]}',
        hasn_id=base_envelope.owner_id,
        event_type=event_type,
        payload={
            'sync_scope_kind': 'owner',
            'sync_scope_id': base_envelope.owner_id,
            'namespace': 'events',
            'record_id': f'owner_event:{base_envelope.owner_id}:1',
            'revision': 1,
        },
        dedupe_key=f'memory:{base_envelope.owner_id}:1',
    )
    await _accept(sessions, envelope)
    store = SQLAlchemySyncStore()
    now = timezone.now()
    async with sessions.begin() as sync_db:
        claimed = await store.claim_inbox(
            sync_db,
            instance_id='memory-crash-after-commit',
            now=now,
            lease_seconds=1,
            event_types=(event_type,),
        )
    assert claimed is not None

    async with sessions.begin() as business_db:
        first = await hasn_sync_business_receipts_service.reserve(
            db=business_db,
            idempotency_key=claimed.idempotency_key,
            owner_id=envelope.owner_id,
            node_id=envelope.node_id,
            client_event_id=envelope.client_event_id,
            event_type=envelope.event_type,
        )
        assert first is True
        await MemorySyncHandler().apply(
            business_db,
            envelope,
            idempotency_key=claimed.idempotency_key,
        )

    async with sessions.begin() as sync_db:
        await sync_db.execute(
            sa.text(
                f'UPDATE {_INBOX} SET locked_at = :expired '
                'WHERE id = :row_id'
            ),
            {
                'expired': now - timedelta(seconds=2),
                'row_id': claimed.row_id,
            },
        )
    assert await _worker(sessions).process_one(now=now) is True

    async with sessions() as db:
        namespace_revision = (
            await db.execute(
                sa.text(
                    'SELECT revision FROM hasn_memory.namespace_revision '
                    "WHERE sync_scope_kind = 'owner' "
                    'AND sync_scope_id = :owner_id '
                    "AND namespace = 'events'"
                ),
                {'owner_id': envelope.owner_id},
            )
        ).scalar_one()
        event_count = (
            await db.execute(
                sa.text(
                    f'SELECT count(*) FROM {_EVENTS} '
                    'WHERE owner_id = :owner_id '
                    'AND event_type = :event_type'
                ),
                {
                    'owner_id': envelope.owner_id,
                    'event_type': envelope.event_type,
                },
            )
        ).scalar_one()
    assert int(namespace_revision) == 1
    assert int(event_count) == 1
    assert await _receipt_count(sessions, envelope) == 1
    assert await _inbox_status(sessions, envelope) == ('applied', 2)
