"""open trending/search 视角化 is_following 的进程内 HTTP E2E（真实 PG，零 mock）。

证明开放路由能从 ``request.scope['user']`` 解析登录浏览者并回填 is_following——
即「刷新后（跨会话）关注态正确」，正是「后端也彻底修掉」要补的那一层 HTTP 外壳。
最小 app + 注入 user 的中间件模拟生产 AuthenticationMiddleware；经 ASGITransport 走
完整 FastAPI HTTP 栈（查询解析 + 依赖注入 + 统一信封）。
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

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_community.api.v1.open.community_ext import router as open_router
from backend.app.hasn_community.service.topic_service import topic_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction, uuid4_str

pytestmark = pytest.mark.asyncio

_USER_ID = 1_200_000_000 + int(uuid.uuid4().int % 800_000_000)

_APP = FastAPI()


@_APP.middleware('http')
async def _inject_user(request: Request, call_next):
    # 模拟 AuthenticationMiddleware：带 X-E2E-Auth 头时把已认证 user 写进 scope。
    if request.headers.get('X-E2E-Auth'):
        request.scope['user'] = SimpleNamespace(id=_USER_ID)
    return await call_next(request)


_APP.include_router(open_router, prefix='/api/v1/community/open')


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    owner_hasn = f'h_vw_{uuid4_str()[:12]}'
    session.add(
        HasnHumans(hasn_id=owner_hasn, star_id=f's_{uuid4_str()[:12]}', user_id=_USER_ID, nickname='Viewer Owner', status='active')
    )
    await session.flush()

    token = 'zzv' + uuid4_str().replace('-', '')[:8]
    t1 = await topic_service.create_topic(session, name=f'{token}甲', description=None, cover_url=None, created_by_hasn_id=owner_hasn)
    t2 = await topic_service.create_topic(session, name=f'{token}乙', description=None, cover_url=None, created_by_hasn_id=owner_hasn)
    await topic_service.follow_topic(session, follower_hasn_id=owner_hasn, topic_id=t1['topic_id'], following=True)
    await session.flush()

    async def _yield_session():
        yield session

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, token=token, t1=t1['topic_id'], t2=t2['topic_id'])
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _items(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, body
    return body['data']['items']


async def test_search_is_following_authenticated(ctx) -> None:
    """带 Owner 认证：已关注话题 is_following True，未关注 False。"""
    items = _items(await ctx.client.get('/api/v1/community/open/topics/search', params={'q': ctx.token}, headers={'X-E2E-Auth': '1'}))
    by_id = {i['topic_id']: i for i in items}
    assert by_id[ctx.t1]['is_following'] is True
    assert by_id[ctx.t2]['is_following'] is False


async def test_search_is_following_anonymous(ctx) -> None:
    """匿名：仍能搜到话题，但不带 is_following 字段。"""
    items = _items(await ctx.client.get('/api/v1/community/open/topics/search', params={'q': ctx.token}))
    assert items, '匿名也应搜到话题'
    assert all('is_following' not in i for i in items), '匿名不应带 is_following'


async def test_trending_is_following_authenticated(ctx) -> None:
    """带 Owner 认证：trending 每条回填 is_following(bool)；匿名不带。"""
    rows = _items(await ctx.client.get('/api/v1/community/open/topics/trending', params={'limit': 50}, headers={'X-E2E-Auth': '1'}))
    assert rows, 'trending 应非空'
    assert all(isinstance(r.get('is_following'), bool) for r in rows)

    anon = _items(await ctx.client.get('/api/v1/community/open/topics/trending', params={'limit': 50}))
    assert all('is_following' not in r for r in anon)
