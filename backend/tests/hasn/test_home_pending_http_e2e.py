"""M4 工作台未处理项聚合 owner read-API 真实 HTTP E2E（真实 PostgreSQL，零 mock）。

GET /api/v1/hasn/app/home/pending 是 hasn.workbench.pending.scan 的 Owner-JWT 只读镜像
（同一 aggregator，同源口径），供 webui 首页「未处理总数」角标 / 简报空态兜底列表，无需跑 LLM。

本测试守卫 **HTTP 接线**（端点可达 → Owner JWT 解析 owner → aggregator → 统一信封 + owner 隔离）；
9 个 provider 各自的口径已由 tests/mcp/test_workbench_pending.py 在 service 层穷尽，这里只 seed 一个
最少 FK 负担的 reel 待处理项（project_id=0 无 FK）做接线证据，不重复 provider 口径。

模块级把 app home 路由挂最小 app，fixture 用 dependency_overrides 注入“每测试唯一”user_id +
真实 PG 会话（照抄 test_home_pref_http_e2e.py）。每测试唯一 user_id 避免累积 human 让
_resolve_owner_id 取错。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/hasn/test_home_pending_http_e2e.py
无 DB 时跳过（不伪造）。

事实源: docs/hasn-node设计文档/13-工作台/05-简报后端聚合(全应用未处理项汇聚)设计.md §3.3/§5 M4。
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
from backend.app.hasn_reel.model.reel_creation import ReelCreation
from backend.app.home.api.v1.app.home import router as app_home_router
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_home_router, prefix='/api/v1/hasn/app')

_PENDING = '/api/v1/hasn/app/home/pending'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 960_000_000 + int(uuid.uuid4().int % 20_000_000)


def _human(hasn_id: str, user_id: int, nickname: str) -> HasnHumans:
    # star_id 与 nickname 均有唯一索引，按 hasn_id 派生唯一值避免与存量空串撞键
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'{nickname}_{hasn_id[-6:]}', status='active'
    )


def _reel(owner: str, title: str, status: str) -> ReelCreation:
    # project_id=0 无 FK 约束（reel 是最少 FK 负担的 provider，适合做接线证据）
    return ReelCreation(project_id=0, owner_hasn_id=owner, kind='agent_tools', title=title, status=status)


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
    owner = f'h_pend_{_uid()}'
    session.add(_human(owner, user_id, '未处理E2E'))
    await session.flush()

    auth_state = {'user_id': user_id}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, owner=owner, session=session, user_id=user_id, auth_state=auth_state)
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


async def test_pending_endpoint_aggregates_and_envelope(env) -> None:
    """seed reel 待处理项（waiting_user）+ 一条已完成（应排除）→ GET 返回统一信封 + reel 分组。"""
    s, owner, c = env.session, env.owner, env.client
    s.add(_reel(owner, '等你回答的短视频', 'waiting_user'))
    s.add(_reel(owner, '已完成短视频（不应出现）', 'succeeded'))
    await s.flush()

    data = _data(await c.get(_PENDING))
    # 结构化契约：PendingScanResult 三键齐全
    assert set(data.keys()) == {'total', 'by_app', 'degraded'}, data
    assert data['degraded'] == [], f'clean 会话不应有 provider 读失败：{data["degraded"]}'
    # reel 只聚 waiting_user 一条，deep_link canonical /apps/reel
    assert 'reel' in data['by_app'], data['by_app']
    reel = data['by_app']['reel']
    assert reel['count'] == 1
    assert reel['items'][0]['title'] == '等你回答的短视频'
    assert reel['items'][0]['deep_link'] == '/apps/reel'
    assert reel['items'][0]['app_id'] == 'reel'
    # total 至少含这一条（其它 provider 对这个新 owner 应为空 → 恰好 1）
    assert data['total'] == 1, data['by_app']


async def test_pending_limit_per_app_clamped(env) -> None:
    """limit_per_app 越界（0 / 超上限）被夹到 [1,50]，端点不报错、仍回信封。"""
    s, owner, c = env.session, env.owner, env.client
    s.add(_reel(owner, '待处理创作', 'waiting_user'))
    await s.flush()

    # 0 → 夹到 1（仍能读到 reel）
    data0 = _data(await c.get(_PENDING, params={'limit_per_app': 0}))
    assert data0['by_app']['reel']['count'] == 1
    # 越上限 → 夹到 50，不炸
    data_big = _data(await c.get(_PENDING, params={'limit_per_app': 9999}))
    assert data_big['by_app']['reel']['count'] == 1


async def test_pending_owner_isolation(env) -> None:
    """owner A 有未处理项；切到 owner B（不同 user_id、无项）→ total=0、by_app 空、degraded 空。"""
    s, owner, c = env.session, env.owner, env.client
    s.add(_reel(owner, 'A 的待处理', 'waiting_user'))
    await s.flush()
    # A 视图：有 reel
    assert _data(await c.get(_PENDING))['by_app'].get('reel', {}).get('count') == 1

    # 切到 owner B（新 user_id + 新 human，无任何未处理项）
    owner_b = f'h_pendB_{_uid()}'
    user_b = _new_user_id()
    s.add(_human(owner_b, user_b, 'B'))
    await s.flush()
    env.auth_state['user_id'] = user_b

    data_b = _data(await c.get(_PENDING))
    assert data_b['total'] == 0, f'B 无未处理项应 total=0（零 fake），实际 {data_b}'
    assert data_b['by_app'] == {}, 'B 不应看到 A 的 reel（owner 隔离）'
    assert data_b['degraded'] == []
