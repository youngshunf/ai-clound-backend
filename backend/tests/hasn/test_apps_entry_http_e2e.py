"""P5 应用入口（注册即用）真实 HTTP E2E（真实 PostgreSQL，零 mock）。

覆盖 `GET /apps`（全部已注册）+ `GET /apps/{app_id}/entry`：
  - knowledge → entry_route + 已配置的 daemon_direct 实例句柄；
  - community → entry_route + gateway_internal；
  - 两者响应均**不含 credential**（凭据不进浏览器，设计 11 §0.3/§7.2）。
  - 未知应用 → 404。
  - `/apps` 返回全部已注册应用（注册即用，非「已挂载」）。

事实源: docs/hasn-node设计文档/13-工作台/04-...设计.md §6/§7；实施清单 §7。
"""
from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.home.api.v1.app.home import router as app_workbench_router
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_workbench_router, prefix='/api/v1/hasn/app')

_APPS = '/api/v1/hasn/app/apps'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 980_000_000 + int(uuid.uuid4().int % 15_000_000)


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id,
        star_id=f's_{hasn_id}',
        user_id=user_id,
        nickname=f'入口E2E_{hasn_id[-6:]}',
        status='active',
    )


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    user_id = _new_user_id()
    owner = f'h_entry_{_uid()}'
    session.add(_human(owner, user_id))
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=user_id)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, owner=owner, session=session, user_id=user_id)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def test_list_apps_returns_all_registered(env) -> None:
    """/apps 返回全部已注册应用（注册即用），含内置 knowledge/community。"""
    apps = _data(await env.client.get(_APPS))
    ids = {a['id'] for a in apps}
    assert 'knowledge' in ids, '应列出内置知识库'
    assert 'community' in ids, '应列出内置社区'
    # 注册即用：每个应用都带 entry_route 供导航。
    assert all(a.get('entry_route') for a in apps)


async def test_entry_builtin_app_returns_internal_handle_without_credential(env) -> None:
    """knowledge entry → daemon_direct 实例句柄 + entry_route，且响应不含凭据。"""
    handle = _data(await env.client.get(f'{_APPS}/knowledge/entry'))
    assert handle['app_id'] == 'knowledge'
    assert handle['entry_route'] == '/apps/knowledge'
    assert handle['transport'] == 'daemon_direct'
    assert str(handle['instance_id']).isdigit()
    assert handle['endpoint']
    assert handle['requires_credential'] is True
    # 凭据绝不下发浏览器。
    assert 'credential' not in handle
    # 当前空间（默认个人）随句柄返回，便于切空间重解析。
    assert handle['workspace']['kind'] in ('personal', 'enterprise')


async def test_entry_unknown_app_returns_404(env) -> None:
    """未知应用 entry → 404（如实，不伪造句柄）。"""
    resp = await env.client.get(f'{_APPS}/does-not-exist/entry')
    assert resp.status_code == 404, resp.text


async def test_entry_community_app(env) -> None:
    """社区应用 entry_route 指向原生社区页（内置）。"""
    handle = _data(await env.client.get(f'{_APPS}/community/entry'))
    assert handle['app_id'] == 'community'
    assert handle['entry_route'] == '/apps/community'
    assert handle['transport'] == 'gateway_internal'
    assert 'credential' not in handle
