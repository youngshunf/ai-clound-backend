"""sync HTTP 入口的真实 JWT、Redis 与 PostgreSQL 权限矩阵。"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.admin.model.user import User
from backend.app.hasn.api.v1.sync import router as sync_router
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
async def auth_matrix():
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
        db.add_all(
            [
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
            ]
        )

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
                sa.text(
                    f'DELETE FROM {_INBOX} '
                    'WHERE owner_id IN (:owner_id, :other_owner_id)'
                ),
                {
                    'owner_id': owner_id,
                    'other_owner_id': other_owner_id,
                },
            )
            await db.execute(
                sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id)
            )
            await db.execute(
                sa.delete(HasnHumans).where(
                    HasnHumans.hasn_id.in_([owner_id, other_owner_id])
                )
            )
            await db.execute(
                sa.delete(User).where(
                    User.id.in_([owner.id, other.id, unbound.id])
                )
            )
        await engine.dispose()


def _bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


async def test_owner_jwt_can_use_general_memory_and_task_sync(
    auth_matrix,
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


async def test_cross_owner_and_unbound_owner_are_rejected_over_http(
    auth_matrix,
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


async def test_agent_jwt_cannot_enter_owner_sync_routes(auth_matrix) -> None:
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
    assert general.status_code == 403, general.text
    assert task.status_code == 403, task.text
