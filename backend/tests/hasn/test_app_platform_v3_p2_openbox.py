"""应用平台 v3 P2「entitlement 收口 / 开箱即用」真实 PostgreSQL HTTP E2E（零 mock）。

验证 `list_apps`（`GET /apps`）改为 **catalog(published) ∩ entitlement**、
**不再叠加 per-workspace 挂载态**后（设计 17 §6.1，实施台账 P2 首项）：

- 免费 app **零挂载记录直出**：从未 enable 也出现在工作台，`status='available'`、`access.allowed=True`
  （开箱即用——挂载概念废除，`hasn_workspace_app` 已于 P3 退役删表）。
- 付费 app **未开通显示付费墙**：仍出现在列表（`status='available'`），但 `access.allowed=False`
  携 `reason/requires/min_tier`，前端据此原位渲染升级/购买（付费墙随列表内联，无需挂载动作）。

注：P2 阶段曾有「disabled 挂载行不影响展示」一档证明展示态与挂载解耦；P3 已删 `hasn_workspace_app`
表（设计 17 决策①），挂载概念彻底不复存在，该档随表退役删除。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §6.1；
实施台账: docs/hasn-node设计文档/14-AI-Native应用平台/实施/08-应用平台v3重构实施.md P2。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.home.api.v1.app.home import router as app_workbench_router
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_humans import HasnHumans
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


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id,
        nickname=f'P2_{hasn_id[-6:]}', status='active',
    )


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    """造一条 published、personal 可见的 catalog 行（默认免费）。"""
    base = {
        'app_id': app_id,
        'name': 'P2 测试应用',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'v3 P2 开箱即用',
        'source': 'first_party',
        'status': 'published',
        'execution_mode': 'cloud',
        'scope': ['personal'],
        'collaboration_mode': 'none',
        'entry_route': f'/apps/{app_id}',
        'sort_order': 500,
        'default_mount': False,
        'requires_role': None,
        'access_type': 'free',
        'min_tier': None,
        'price_amount': None,
        'price_unit': 'cny',
        'billing_cycle': 'once',
        'trial_days': 0,
        'sku_ref': None,
        'manifest_present': False,
    }
    base.update(over)
    return HasnAppCatalog(**base)


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    user_id = 960_000_000 + int(uuid.uuid4().int % 15_000_000)
    owner = f'h_p2_{_uid()}'
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
        yield SimpleNamespace(client=client, session=session, user_id=user_id, owner=owner)
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


async def _list(env) -> dict[str, dict]:
    apps = _data(await env.client.get(_APPS))
    return {a['id']: a for a in apps}


async def test_free_app_available_without_mount(env) -> None:
    """免费 app 零挂载记录也直出：status='available' + access.allowed=True（开箱即用）。"""
    app_id = f'p2free_{_uid()}'
    env.session.add(_catalog(app_id, access_type='free'))
    await env.session.flush()

    by_id = await _list(env)
    assert app_id in by_id, '免费 app 从未挂载也应出现在工作台（开箱即用）'
    app = by_id[app_id]
    assert app['status'] == 'available'
    assert app['access']['allowed'] is True
    assert app['access']['reason'] == 'free'


async def test_paid_app_shows_paywall_inline(env) -> None:
    """付费(tier)未开通：仍在列表(status='available')，但 access 携付费墙信息。"""
    app_id = f'p2tier_{_uid()}'
    env.session.add(_catalog(app_id, access_type='tier', min_tier='pro', trial_days=7))
    await env.session.flush()  # owner 无订阅 → 有效档位 free < pro

    by_id = await _list(env)
    assert app_id in by_id, '付费 app 未开通也应在列表中展示（付费墙原位渲染）'
    access = by_id[app_id]['access']
    assert by_id[app_id]['status'] == 'available'
    assert access['allowed'] is False
    assert access['reason'] == 'need_upgrade'
    assert access['requires'] == 'upgrade'
    assert access['min_tier'] == 'pro'
    assert access['trial_available'] is True
