"""hasn_sync pull/push/full-refresh/retention 真实 PostgreSQL 契约。"""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_sync.adapters.sqlalchemy_appender import SqlAlchemySyncAppender
from backend.app.hasn_sync.application.pull import pull_events
from backend.app.hasn_sync.application.push import accept_envelopes
from backend.app.hasn_sync.application.retention import run_retention
from backend.app.hasn_sync.domain.cursor import CursorError
from backend.app.hasn_sync.ports.dto import InboxEnvelope, SyncEnvelope
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone


pytestmark = pytest.mark.asyncio
_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


@pytest_asyncio.fixture
async def sync_sessions():
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        future=True,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _owner() -> str:
    return f'h_sk{uuid.uuid4().hex[:18]}'


def _sync_envelope(owner: str, number: int) -> SyncEnvelope:
    return SyncEnvelope(
        owner_id=owner,
        hasn_id=owner,
        event_type='kernel.contract.v1',
        aggregate_type='probe',
        aggregate_id=f'probe-{number}',
        payload={'number': number, 'nested': {'complete': True}},
        producer='sync_contract',
        source_event_id=f'source-{number}-{uuid.uuid4().hex[:8]}',
    )


async def _cleanup(sessionmaker, owner: str) -> None:
    async with sessionmaker.begin() as db:
        await db.execute(
            sa.text(f'DELETE FROM {_INBOX} WHERE owner_id = :owner'),
            {'owner': owner},
        )
        await db.execute(
            sa.text(f'DELETE FROM {_EVENTS} WHERE owner_id = :owner'),
            {'owner': owner},
        )


async def test_pull_returns_stored_complete_payload_without_business_lookup(
    sync_sessions,
) -> None:
    owner = _owner()
    appender = SqlAlchemySyncAppender()
    try:
        async with sync_sessions.begin() as db:
            await appender.append(db, _sync_envelope(owner, 1))
            await appender.append(db, _sync_envelope(owner, 2))

        async with sync_sessions() as db:
            page = await pull_events(
                db,
                owner_id=owner,
                cursor=None,
                limit=1,
            )
        assert page.full_refresh is None
        assert page.has_more is True
        assert page.next_cursor == f'owner:{owner}:1'
        assert [event.payload for event in page.events] == [
            {'number': 1, 'nested': {'complete': True}}
        ]
    finally:
        await _cleanup(sync_sessions, owner)


async def test_expired_and_ahead_cursor_return_explicit_full_refresh(
    sync_sessions,
) -> None:
    owner = _owner()
    appender = SqlAlchemySyncAppender()
    try:
        async with sync_sessions.begin() as db:
            for number in range(1, 4):
                await appender.append(db, _sync_envelope(owner, number))
            await db.execute(
                sa.text(
                    f'DELETE FROM {_EVENTS} '
                    'WHERE owner_id = :owner AND revision = 1'
                ),
                {'owner': owner},
            )

        async with sync_sessions() as db:
            expired = await pull_events(
                db,
                owner_id=owner,
                cursor=f'owner:{owner}:0',
                limit=100,
            )
            ahead = await pull_events(
                db,
                owner_id=owner,
                cursor=f'owner:{owner}:99',
                limit=100,
            )
        assert expired.events == ()
        assert expired.full_refresh is not None
        assert expired.full_refresh.reason == 'cursor_expired'
        assert expired.full_refresh.min_available_revision == 2
        assert expired.full_refresh.head_revision == 3
        assert ahead.full_refresh is not None
        assert ahead.full_refresh.reason == 'cursor_ahead'
    finally:
        await _cleanup(sync_sessions, owner)


async def test_cursor_cannot_cross_owner(sync_sessions) -> None:
    owner = _owner()
    with pytest.raises(CursorError, match='owner'):
        async with sync_sessions() as db:
            await pull_events(
                db,
                owner_id=owner,
                cursor='owner:h_another:0',
                limit=10,
            )


async def test_push_only_persists_generic_inbox_and_detects_conflict(
    sync_sessions,
) -> None:
    owner = _owner()
    client_event_id = f'ce_{uuid.uuid4().hex[:20]}'
    envelope = InboxEnvelope(
        owner_id=owner,
        node_id='node-r3-contract',
        client_event_id=client_event_id,
        hasn_id=owner,
        event_type='opaque.business.event.v1',
        payload={'opaque': {'value': 1}},
        dedupe_key='business-key-1',
    )
    changed = InboxEnvelope(
        owner_id=owner,
        node_id=envelope.node_id,
        client_event_id=client_event_id,
        hasn_id=owner,
        event_type=envelope.event_type,
        payload={'opaque': {'value': 2}},
        dedupe_key=envelope.dedupe_key,
    )
    try:
        async with sync_sessions.begin() as db:
            first = await accept_envelopes(db, (envelope,))
        async with sync_sessions.begin() as db:
            duplicate = await accept_envelopes(db, (envelope,))
        async with sync_sessions.begin() as db:
            conflict = await accept_envelopes(db, (changed,))

        assert [item.status for item in first.items] == ['accepted']
        assert [item.status for item in duplicate.items] == ['duplicate']
        assert [item.status for item in conflict.items] == ['conflict']
        async with sync_sessions() as db:
            row = (
                await db.execute(
                    sa.text(
                        f'SELECT event_type, payload, status FROM {_INBOX} '
                        'WHERE owner_id = :owner'
                    ),
                    {'owner': owner},
                )
            ).mappings().one()
        assert row['event_type'] == 'opaque.business.event.v1'
        assert row['payload'] == {'opaque': {'value': 1}}
        assert row['status'] == 'accepted'
    finally:
        await _cleanup(sync_sessions, owner)


async def test_retention_deletes_only_expired_events(sync_sessions) -> None:
    owner = _owner()
    appender = SqlAlchemySyncAppender()
    now = timezone.now()
    try:
        async with sync_sessions.begin() as db:
            expired = await appender.append(db, _sync_envelope(owner, 1))
            kept = await appender.append(db, _sync_envelope(owner, 2))
            await db.execute(
                sa.text(
                        f'UPDATE {_EVENTS} '
                        'SET expires_at = CASE '
                        'WHEN revision = :expired_revision '
                        'THEN CAST(:expired_at AS timestamptz) '
                        'ELSE CAST(:kept_until AS timestamptz) END '
                    'WHERE owner_id = :owner'
                ),
                {
                    'owner': owner,
                    'expired_revision': expired.revision,
                    'expired_at': now - timedelta(seconds=1),
                    'kept_until': now + timedelta(hours=1),
                },
            )
        async with sync_sessions.begin() as db:
            result = await run_retention(db, now=now, batch_size=100)
        assert result.deleted == 1
        async with sync_sessions() as db:
            revisions = (
                await db.execute(
                    sa.text(
                        f'SELECT revision FROM {_EVENTS} '
                        'WHERE owner_id = :owner ORDER BY revision'
                    ),
                    {'owner': owner},
                )
            ).scalars().all()
        assert revisions == [kept.revision]
    finally:
        await _cleanup(sync_sessions, owner)
