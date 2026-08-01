"""sync HTTP 入口的真实 JWT、Redis 与 PostgreSQL 权限矩阵。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.admin.model.user import User
from backend.app.hasn.api.v1.sync import router as sync_router
from backend.app.hasn.model import HasnImHistorySnapshots
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_task.api.v1.app.sync import router as task_sync_router
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt import (
    create_agent_access_token,
    revoke_agent_token,
)
from backend.common.security.jwt import (
    create_access_token,
    revoke_token,
)
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.redis import redis_client
from backend.database.schema_names import SCHEMA_NAMES
from backend.middleware.jwt_auth_middleware import JwtAuthMiddleware

pytestmark = pytest.mark.asyncio(loop_scope='session')
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(sync_router, prefix='/api/v1/hasn')
    app.include_router(task_sync_router, prefix='/api/v1/hasn-task/app')
    register_exception(app)
    app.add_middleware(
        AuthenticationMiddleware,
        backend=JwtAuthMiddleware(),
        on_error=JwtAuthMiddleware.auth_exception_handler,
    )
    app.add_middleware(
        ContextMiddleware,
        plugins=[RequestIdPlugin(validate=False)],
    )
    return app


_APP = _build_app()


@pytest_asyncio.fixture(scope='module', loop_scope='session')
async def auth_matrix() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        future=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    owner_id = f'h_http_{marker[:20]}'
    other_owner_id = f'h_http_{marker[2:22]}'
    agent_id = f'a_http_{marker[:20]}'
    owner_username = f'im_http_owner_{marker[:16]}'
    other_username = f'im_http_other_{marker[:16]}'
    unbound_username = f'im_http_unbound_{marker[:16]}'
    async with sessions.begin() as db:
        owner = User(
            username=owner_username,
            nickname=f'同步主人{marker[:10]}',
            password=None,
            salt=None,
        )
        other = User(
            username=other_username,
            nickname=f'同步他人{marker[:10]}',
            password=None,
            salt=None,
        )
        unbound = User(
            username=unbound_username,
            nickname=f'未绑定用户{marker[:10]}',
            password=None,
            salt=None,
        )
        db.add_all([owner, other, unbound])
        await db.flush()
        db.add_all([
            HasnHumans(
                hasn_id=owner_id,
                star_id=f'h{marker[:24]}',
                user_id=owner.id,
                nickname=owner.nickname,
                status='active',
            ),
            HasnHumans(
                hasn_id=other_owner_id,
                star_id=f'o{marker[:24]}',
                user_id=other.id,
                nickname=other.nickname,
                status='active',
            ),
            HasnAgents(
                hasn_id=agent_id,
                star_id=f'a{marker[:24]}',
                owner_id=owner_id,
                display_name='HTTP 权限矩阵分身',
                agent_name=f'agent{marker[:10]}',
                status='active',
            ),
        ])

    owner_token = await create_access_token(owner.id, multi_login=True)
    other_token = await create_access_token(other.id, multi_login=True)
    unbound_token = await create_access_token(unbound.id, multi_login=True)
    agent_token = await create_agent_access_token(
        agent_hasn_id=agent_id,
        agent_name='HTTP 权限矩阵分身',
        owner_hasn_id=owner_id,
        owner_user_id=owner.id,
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_APP),
        base_url='http://sync-auth-e2e',
    )
    context = SimpleNamespace(
        client=client,
        owner_id=owner_id,
        other_owner_id=other_owner_id,
        agent_id=agent_id,
        owner_user_id=owner.id,
        other_user_id=other.id,
        unbound_user_id=unbound.id,
        owner_token=owner_token,
        other_token=other_token,
        unbound_token=unbound_token,
        agent_token=agent_token,
    )
    try:
        yield context
    finally:
        await client.aclose()
        await revoke_token(owner.id, owner_token.session_uuid)
        await revoke_token(other.id, other_token.session_uuid)
        await revoke_token(unbound.id, unbound_token.session_uuid)
        await revoke_agent_token(agent_id, agent_token.session_uuid)
        await redis_client.delete(
            f'{settings.JWT_USER_REDIS_PREFIX}:{owner.id}',
            f'{settings.JWT_USER_REDIS_PREFIX}:{other.id}',
            f'{settings.JWT_USER_REDIS_PREFIX}:{unbound.id}',
        )
        async with sessions.begin() as db:
            await db.execute(
                sa.delete(HasnImHistorySnapshots).where(HasnImHistorySnapshots.owner_id.in_([owner_id, other_owner_id]))
            )
            await db.execute(
                sa.text(f'DELETE FROM {_INBOX} WHERE owner_id IN (:owner_id, :other_owner_id)'),
                {
                    'owner_id': owner_id,
                    'other_owner_id': other_owner_id,
                },
            )
            await db.execute(sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id))
            await db.execute(sa.delete(HasnHumans).where(HasnHumans.hasn_id.in_([owner_id, other_owner_id])))
            await db.execute(sa.delete(User).where(User.id.in_([owner.id, other.id, unbound.id])))
        await engine.dispose()


def _bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


async def test_owner_jwt_can_use_general_memory_and_task_sync(
    auth_matrix: SimpleNamespace,
) -> None:
    """真实 Owner JWT 可访问三个 owner 同步入口。"""
    owner = auth_matrix.owner_id
    headers = _bearer(auth_matrix.owner_token.access_token)
    general = await auth_matrix.client.post(
        '/api/v1/hasn/sync/pull',
        headers=headers,
        json={'owner_id': owner, 'cursor': None, 'limit': 10},
    )
    memory = await auth_matrix.client.post(
        '/api/v1/hasn/memory/sync/pull',
        headers=headers,
        json={
            'owner_id': owner,
            'agent_ids': [],
            'namespaces': [],
            'cursors': [],
            'max_events': 10,
        },
    )
    task_pull = await auth_matrix.client.post(
        '/api/v1/hasn-task/app/sync/pull',
        headers=headers,
        json={
            'owner_id': owner,
            'node_id': 'node-http-auth-pg',
            'cursor': None,
            'limit': 10,
        },
    )
    task_push = await auth_matrix.client.post(
        '/api/v1/hasn-task/app/sync/push',
        headers=headers,
        json={
            'owner_id': owner,
            'node_id': 'node-http-auth-pg',
            'events': [],
        },
    )
    assert general.status_code == 200, general.text
    assert memory.status_code == 200, memory.text
    assert task_pull.status_code == 200, task_pull.text
    assert task_push.status_code == 200, task_push.text


async def test_sync_push_rejection_carries_client_event_id(
    auth_matrix: SimpleNamespace,
) -> None:
    """拒绝结果必须带 `detail.client_event_id`（hasn-node 实施/98）。

    daemon 据此逐事件处置：永久拒绝丢弃、冲突退避；缺这个锚点就只能整批扣留重推——
    正是本地画像事件被 8040 拒绝后每 5 秒重推刷屏的放大器。
    """
    owner = auth_matrix.owner_id
    headers = _bearer(auth_matrix.owner_token.access_token)
    general = await auth_matrix.client.post(
        '/api/v1/hasn/sync/push',
        headers=headers,
        json={
            'owner_id': owner,
            'node_id': 'node-http-auth-pg',
            'events': [
                {
                    'client_event_id': 'ce_unsupported_portrait_1',
                    'event_type': 'memory.agent_self_portrait.upserted',
                    'payload': {},
                }
            ],
        },
    )
    task = await auth_matrix.client.post(
        '/api/v1/hasn-task/app/sync/push',
        headers=headers,
        json={
            'owner_id': owner,
            'node_id': 'node-http-auth-pg',
            'events': [
                {
                    'client_event_id': 'ce_unsupported_task_1',
                    'event_type': 'task.not_a_real_event',
                    'payload': {},
                }
            ],
        },
    )

    assert general.status_code == 200, general.text
    general_body = general.json()
    assert general_body['accepted'] == 0
    assert [item['name'] for item in general_body['rejected']] == ['ERR_SYNC_EVENT_UNSUPPORTED']
    assert general_body['rejected'][0]['detail'] == {'client_event_id': 'ce_unsupported_portrait_1'}

    assert task.status_code == 200, task.text
    task_body = task.json()
    assert task_body['accepted'] == 0
    assert [item['name'] for item in task_body['rejected']] == ['ERR_TASK_SYNC_EVENT_UNSUPPORTED']
    assert task_body['rejected'][0]['detail'] == {'client_event_id': 'ce_unsupported_task_1'}


async def test_owner_jwt_can_page_message_history_snapshot(
    auth_matrix: SimpleNamespace,
) -> None:
    """真实 Owner JWT 可建立并分页读取自己的消息历史快照。"""
    owner = auth_matrix.owner_id
    headers = _bearer(auth_matrix.owner_token.access_token)
    started = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/start',
        headers=headers,
        json={'owner_id': owner},
    )
    assert started.status_code == 200, started.text
    started_body = started.json()
    assert started_body['snapshot_token']
    assert started_body['head_revision'] >= 0
    assert started_body['conversation_count'] >= 0
    assert started_body['message_count'] >= 0
    assert isinstance(started_body['history_complete'], bool)
    assert started_body['head_cursor'] == f'owner:{owner}:{started_body["head_revision"]}'

    conversations = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/conversations',
        headers=headers,
        json={
            'owner_id': owner,
            'snapshot_token': started_body['snapshot_token'],
            'cursor': None,
            'limit': 10,
        },
    )
    messages = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/messages',
        headers=headers,
        json={
            'owner_id': owner,
            'snapshot_token': started_body['snapshot_token'],
            'cursor': None,
            'limit': 10,
        },
    )
    assert conversations.status_code == 200, conversations.text
    assert messages.status_code == 200, messages.text
    assert isinstance(conversations.json()['items'], list)
    assert isinstance(messages.json()['items'], list)


async def test_message_history_snapshot_rejects_cross_owner(
    auth_matrix: SimpleNamespace,
) -> None:
    """请求体 owner 不能替换真实 JWT 主人。"""
    response = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/start',
        headers=_bearer(auth_matrix.owner_token.access_token),
        json={'owner_id': auth_matrix.other_owner_id},
    )
    assert response.status_code == 403, response.text


async def test_message_history_snapshot_rejects_tampered_and_expired_tokens(
    auth_matrix: SimpleNamespace,
) -> None:
    """历史分页必须拒绝签名被篡改或已经过期的快照令牌。"""
    owner = auth_matrix.owner_id
    headers = _bearer(auth_matrix.owner_token.access_token)
    started = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/start',
        headers=headers,
        json={'owner_id': owner},
    )
    assert started.status_code == 200, started.text
    snapshot_token = started.json()['snapshot_token']
    tampered_token = (
        snapshot_token[:-1]
        + ('a' if snapshot_token[-1] != 'a' else 'b')
    )
    expired_token = jwt.encode(
        {
            'kind': 'im_history_snapshot_v2',
            'snapshot_id': str(uuid.uuid4()),
            'owner_id': owner,
            'exp': 1,
        },
        settings.TOKEN_SECRET_KEY,
        algorithm=settings.TOKEN_ALGORITHM,
    )

    for invalid_token in (tampered_token, expired_token):
        response = await auth_matrix.client.post(
            '/api/v1/hasn/sync/im/bootstrap/messages',
            headers=headers,
            json={
                'owner_id': owner,
                'snapshot_token': invalid_token,
                'cursor': None,
                'limit': 10,
            },
        )
        assert response.status_code == 400, response.text


async def test_cross_owner_and_unbound_owner_are_rejected_over_http(
    auth_matrix: SimpleNamespace,
) -> None:
    """请求体不能替换 JWT owner，未绑定用户也不能进入同步数据面。"""
    cross = await auth_matrix.client.post(
        '/api/v1/hasn/sync/pull',
        headers=_bearer(auth_matrix.owner_token.access_token),
        json={
            'owner_id': auth_matrix.other_owner_id,
            'cursor': None,
            'limit': 10,
        },
    )
    unbound = await auth_matrix.client.post(
        '/api/v1/hasn-task/app/sync/push',
        headers=_bearer(auth_matrix.unbound_token.access_token),
        json={
            'owner_id': auth_matrix.owner_id,
            'node_id': 'node-http-auth-pg',
            'events': [],
        },
    )
    assert cross.status_code == 403, cross.text
    assert unbound.status_code == 403, unbound.text


async def test_agent_jwt_cannot_enter_owner_sync_routes(
    auth_matrix: SimpleNamespace,
) -> None:
    """真实 Agent JWT 在 Owner sync 路由必须被拒绝。"""
    headers = _bearer(auth_matrix.agent_token.access_token)
    general = await auth_matrix.client.post(
        '/api/v1/hasn/sync/pull',
        headers=headers,
        json={
            'owner_id': auth_matrix.owner_id,
            'cursor': None,
            'limit': 10,
        },
    )
    task = await auth_matrix.client.post(
        '/api/v1/hasn-task/app/sync/push',
        headers=headers,
        json={
            'owner_id': auth_matrix.owner_id,
            'node_id': 'node-http-auth-pg',
            'events': [],
        },
    )
    bootstrap = await auth_matrix.client.post(
        '/api/v1/hasn/sync/im/bootstrap/start',
        headers=headers,
        json={'owner_id': auth_matrix.owner_id},
    )
    assert general.status_code == 403, general.text
    assert task.status_code == 403, task.text
    assert bootstrap.status_code == 403, bootstrap.text
