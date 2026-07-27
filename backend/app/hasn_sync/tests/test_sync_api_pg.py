"""sync API 入口到 inbox worker 的真实 PostgreSQL 接线测试。"""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from backend.app.hasn.api.v1.sync import pull_sync_events, push_sync_events
from backend.app.hasn.schema.hasn_sync import (
    ClientEvent,
    SyncPullRequest,
    SyncPushRequest,
)
from backend.app.hasn.sync_inbox_worker import SyncInboxWorker
from backend.app.hasn_task.api.v1.app.sync import push_task_sync_events
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES


pytestmark = pytest.mark.asyncio
_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


@pytest_asyncio.fixture
async def api_sessions():
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


def _request(owner_id: str | None, *, node_id: str = 'node-sync-api-pg') -> Request:
    return Request(
        {
            'type': 'http',
            'http_version': '1.1',
            'method': 'POST',
            'scheme': 'http',
            'path': '/api/v1/hasn/sync/push',
            'raw_path': b'/api/v1/hasn/sync/push',
            'query_string': b'',
            'headers': [(b'x-node-id', node_id.encode())],
            'client': ('127.0.0.1', 12345),
            'server': ('testserver', 80),
            'user': SimpleNamespace(id=900_300_001, hasn_id=owner_id),
        }
    )


async def _cleanup(sessions, owner_id: str, session_id: str) -> None:
    async with sessions.begin() as db:
        await db.execute(
            sa.text(
                'DELETE FROM public.hasn_sync_business_receipts '
                'WHERE owner_id = :owner_id'
            ),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(f'DELETE FROM {_INBOX} WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(f'DELETE FROM {_EVENTS} WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(
                'DELETE FROM public.hasn_sessions WHERE session_id = :session_id'
            ),
            {'session_id': session_id},
        )
        await db.execute(
            sa.text('DELETE FROM public.hasn_agents WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text('DELETE FROM public.hasn_humans WHERE hasn_id = :owner_id'),
            {'owner_id': owner_id},
        )


async def _cleanup_task(sessions, owner_id: str, task_id: str) -> None:
    async with sessions.begin() as db:
        await db.execute(
            sa.text(
                'DELETE FROM public.hasn_sync_business_receipts '
                'WHERE owner_id = :owner_id'
            ),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(f'DELETE FROM {_INBOX} WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(f'DELETE FROM {_EVENTS} WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text(
                'DELETE FROM hasn_task.assignment '
                'WHERE task_uuid = :task_id'
            ),
            {'task_id': task_id},
        )
        await db.execute(
            sa.text('DELETE FROM hasn_task.task WHERE task_uuid = :task_id'),
            {'task_id': task_id},
        )
        await db.execute(
            sa.text('DELETE FROM public.hasn_agents WHERE owner_id = :owner_id'),
            {'owner_id': owner_id},
        )
        await db.execute(
            sa.text('DELETE FROM public.hasn_humans WHERE hasn_id = :owner_id'),
            {'owner_id': owner_id},
        )


async def _seed_identity(sessions, owner_id: str, agent_id: str) -> None:
    marker = uuid.uuid4().hex
    async with sessions.begin() as db:
        await db.execute(
            sa.text(
                'INSERT INTO public.hasn_humans ('
                'hasn_id, star_id, user_id, nickname, status'
                ') VALUES ('
                ':owner_id, :owner_star_id, :user_id, :nickname, :status'
                ')'
            ),
            {
                'owner_id': owner_id,
                'owner_star_id': f'h{marker[:24]}',
                'user_id': int(marker[:15], 16),
                'nickname': f'sync测试{marker[:12]}',
                'status': 'active',
            },
        )
        await db.execute(
            sa.text(
                'INSERT INTO public.hasn_agents ('
                'hasn_id, star_id, owner_id, display_name, agent_name, '
                'api_key_hash, status, created_via'
                ') VALUES ('
                ':agent_id, :agent_star_id, :owner_id, :display_name, '
                ':agent_name, :api_key_hash, :status, :created_via'
                ')'
            ),
            {
                'agent_id': agent_id,
                'agent_star_id': f'a{marker[:24]}',
                'owner_id': owner_id,
                'display_name': 'sync API 测试分身',
                'agent_name': f't{marker[:12]}',
                'api_key_hash': marker,
                'status': 'active',
                'created_via': 'client',
            },
        )


async def test_owner_entry_push_worker_pull_uses_separate_transactions(
    api_sessions,
) -> None:
    """入口只落 inbox，worker 后置应用，pull 最终返回完整 session.sync。"""
    owner_id = f'h_api{uuid.uuid4().hex[:18]}'
    agent_id = f'a_api{uuid.uuid4().hex[:18]}'
    session_id = f'sess_{uuid.uuid4().hex[:20]}'
    client_event_id = f'ce_{uuid.uuid4().hex[:20]}'
    request = SyncPushRequest(
        owner_id=owner_id,
        node_id='node-sync-api-pg',
        events=[
            ClientEvent(
                client_event_id=client_event_id,
                event_type='session.sync',
                hasn_id=agent_id,
                dedupe_key=session_id,
                payload={
                    'session_id': session_id,
                    'owner_id': owner_id,
                    'hasn_id': agent_id,
                    'session_kind': 'interactive',
                    'session_scope': 'summary_only',
                    'session_status': 'active',
                    'origin_type': 'api',
                    'title': 'sync API 真实接线',
                    'summary_checkpoint_json': {'revision': 1},
                },
            )
        ],
    )
    try:
        await _seed_identity(api_sessions, owner_id, agent_id)
        async with api_sessions.begin() as sync_db:
            response = await push_sync_events(
                _request(owner_id),
                sync_db,
                request,
            )
        assert response.accepted == 1
        async with api_sessions() as db:
            inbox_status = (
                await db.execute(
                    sa.text(
                        f'SELECT status FROM {_INBOX} '
                        'WHERE owner_id = :owner_id '
                        'AND client_event_id = :client_event_id'
                    ),
                    {
                        'owner_id': owner_id,
                        'client_event_id': client_event_id,
                    },
                )
            ).scalar_one()
            business_count = (
                await db.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_sessions '
                        'WHERE session_id = :session_id'
                    ),
                    {'session_id': session_id},
                )
            ).scalar_one()
        assert inbox_status == 'accepted'
        assert int(business_count) == 0

        worker = SyncInboxWorker(
            sync_session_factory=api_sessions,
            business_session_factory=api_sessions,
            instance_id='sync-api-pg',
        )
        assert await worker.process_one() is True

        async with api_sessions() as sync_db:
            pulled = await pull_sync_events(
                _request(owner_id),
                sync_db,
                SyncPullRequest(owner_id=owner_id, cursor=None, limit=10),
            )
        assert [event.event_type for event in pulled.events] == ['session.sync']
        assert pulled.events[0].payload['session_id'] == session_id
        assert pulled.events[0].payload['owner_id'] == owner_id
    finally:
        await _cleanup(api_sessions, owner_id, session_id)


async def test_body_cannot_replace_jwt_owner_and_unbound_owner_is_rejected(
    api_sessions,
) -> None:
    """跨主人请求与未绑定 HASN 身份均在入口、接触数据库前拒绝。"""
    body = SyncPullRequest(owner_id='h_body_owner', cursor=None, limit=10)
    async with api_sessions() as db:
        with pytest.raises(errors.ForbiddenError):
            await pull_sync_events(_request('h_jwt_owner'), db, body)
        with pytest.raises(errors.ForbiddenError):
            await pull_sync_events(_request(None), db, body)


async def test_task_push_worker_applies_once_and_replay_does_not_bump_revision(
    api_sessions,
) -> None:
    """任务上行先落 inbox；业务提交后重放不会重复递增任务修订号。"""
    owner_id = f'h_task_api{uuid.uuid4().hex[:14]}'
    agent_id = f'a_task_api{uuid.uuid4().hex[:14]}'
    task_id = f'task_{uuid.uuid4().hex[:20]}'
    client_event_id = f'ce_task_{uuid.uuid4().hex[:16]}'
    body = SyncPushRequest(
        owner_id=owner_id,
        node_id='node-sync-api-pg',
        events=[
            ClientEvent(
                client_event_id=client_event_id,
                event_type='task.created',
                hasn_id=agent_id,
                dedupe_key=task_id,
                payload={
                    'task_id': task_id,
                    'owner_id': owner_id,
                    'agent_id': agent_id,
                    'name': '真实任务同步',
                    'prompt': '执行真实 PostgreSQL 接线验证',
                    'schedule_type': 'once',
                    'schedule_config': {},
                    'state': 'scheduled',
                },
            )
        ],
    )
    try:
        await _seed_identity(api_sessions, owner_id, agent_id)
        async with api_sessions.begin() as sync_db:
            response = await push_task_sync_events(
                _request(owner_id),
                sync_db,
                body,
            )
        assert response.accepted == 1

        worker = SyncInboxWorker(
            sync_session_factory=api_sessions,
            business_session_factory=api_sessions,
            instance_id='task-sync-api-pg',
        )
        assert await worker.process_one() is True
        async with api_sessions() as db:
            task_row = (
                await db.execute(
                    sa.text(
                        'SELECT task_revision, owner_id, agent_id, name '
                        'FROM hasn_task.task WHERE task_uuid = :task_id'
                    ),
                    {'task_id': task_id},
                )
            ).mappings().one()
            event_count = (
                await db.execute(
                    sa.text(
                        f'SELECT count(*) FROM {_EVENTS} '
                        'WHERE owner_id = :owner_id '
                        "AND event_type = 'task.created' "
                        'AND aggregate_id = :task_id'
                    ),
                    {'owner_id': owner_id, 'task_id': task_id},
                )
            ).scalar_one()
            receipt_count = (
                await db.execute(
                    sa.text(
                        'SELECT count(*) '
                        'FROM public.hasn_sync_business_receipts '
                        'WHERE owner_id = :owner_id'
                    ),
                    {'owner_id': owner_id},
                )
            ).scalar_one()
        assert dict(task_row) == {
            'task_revision': 1,
            'owner_id': owner_id,
            'agent_id': agent_id,
            'name': '真实任务同步',
        }
        assert int(event_count) == 1
        assert int(receipt_count) == 1

        async with api_sessions.begin() as sync_db:
            replay = await push_task_sync_events(
                _request(owner_id),
                sync_db,
                body,
            )
        assert replay.accepted == 1
        assert await worker.process_one() is False
        async with api_sessions() as db:
            unchanged = (
                await db.execute(
                    sa.text(
                        'SELECT task_revision FROM hasn_task.task '
                        'WHERE task_uuid = :task_id'
                    ),
                    {'task_id': task_id},
                )
            ).scalar_one()
        assert int(unchanged) == 1
    finally:
        await _cleanup_task(api_sessions, owner_id, task_id)


async def test_task_worker_rejects_agent_owned_by_another_owner(
    api_sessions,
) -> None:
    """Owner JWT 信封不得把其他主人的分身写成自己的任务执行者。"""
    owner_id = f'h_task_owner{uuid.uuid4().hex[:12]}'
    owner_agent_id = f'a_task_owner{uuid.uuid4().hex[:12]}'
    other_owner_id = f'h_task_other{uuid.uuid4().hex[:12]}'
    other_agent_id = f'a_task_other{uuid.uuid4().hex[:12]}'
    task_id = f'task_{uuid.uuid4().hex[:20]}'
    client_event_id = f'ce_task_{uuid.uuid4().hex[:16]}'
    await _seed_identity(api_sessions, owner_id, owner_agent_id)
    await _seed_identity(api_sessions, other_owner_id, other_agent_id)
    body = SyncPushRequest(
        owner_id=owner_id,
        node_id='node-sync-api-pg',
        events=[
            ClientEvent(
                client_event_id=client_event_id,
                event_type='task.created',
                hasn_id=other_agent_id,
                payload={
                    'task_id': task_id,
                    'owner_id': owner_id,
                    'agent_id': other_agent_id,
                    'name': '越权任务',
                    'prompt': '该任务必须被拒绝',
                    'schedule_type': 'once',
                    'schedule_config': {},
                    'state': 'scheduled',
                },
            )
        ],
    )
    try:
        async with api_sessions.begin() as sync_db:
            response = await push_task_sync_events(
                _request(owner_id),
                sync_db,
                body,
            )
        assert response.accepted == 1
        worker = SyncInboxWorker(
            sync_session_factory=api_sessions,
            business_session_factory=api_sessions,
            instance_id='task-sync-owner-boundary-pg',
        )
        assert await worker.process_one() is True
        async with api_sessions() as db:
            inbox = (
                await db.execute(
                    sa.text(
                        f'SELECT status, last_error FROM {_INBOX} '
                        'WHERE owner_id = :owner_id '
                        'AND client_event_id = :client_event_id'
                    ),
                    {
                        'owner_id': owner_id,
                        'client_event_id': client_event_id,
                    },
                )
            ).mappings().one()
            task_count = (
                await db.execute(
                    sa.text(
                        'SELECT count(*) FROM hasn_task.task '
                        'WHERE task_uuid = :task_id'
                    ),
                    {'task_id': task_id},
                )
            ).scalar_one()
        assert inbox['status'] == 'dead'
        assert '不属于认证主人' in str(inbox['last_error'])
        assert int(task_count) == 0
    finally:
        await _cleanup_task(api_sessions, owner_id, task_id)
        await _cleanup_task(api_sessions, other_owner_id, task_id)
