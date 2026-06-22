"""C2 目录权威切换 真实 PostgreSQL HTTP E2E（零 mock）。

验证 `list_workbench_apps` / `resolve_app_entry` 改读 `hasn_app_catalog`（DB 权威）后：
- 工作台列表与现状**字段等价**（manifest 形状不变 + 携带 launch overlay 字段）。
- catalog `status=disabled` 的应用从列表/入口消失（下架即不可用，设计 §5.2/§6.3）。
- 入口 `entry_route` 取自 catalog。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §6.3；实施清单 §C2。
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

from backend.app.workbench.api.v1.app.workbench import router as app_workbench_router
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

_APPS = '/api/v1/hasn/app/workbench/apps'

# 现状 manifest（WorkbenchApp.to_manifest）的字段集合——C2 必须保持等价。
_MANIFEST_KEYS = {
    'id', 'name', 'icon', 'description', 'scope', 'collaboration_mode',
    'entry_route', 'install_policy', 'requires_role', 'execution_mode',
    'ui_kind', 'window_url', 'window_origin',
}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id,
        nickname=f'C2_{hasn_id[-6:]}', status='active',
    )


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
    user_id = 970_000_000 + int(uuid.uuid4().int % 15_000_000)
    owner = f'h_c2_{_uid()}'
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
        yield SimpleNamespace(client=client, session=session, user_id=user_id)
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


async def test_list_apps_field_equivalence_from_catalog(env) -> None:
    """工作台列表改读 catalog 后字段与现状等价，含内置 knowledge/community + launch overlay。"""
    apps = _data(await env.client.get(_APPS))
    by_id = {a['id']: a for a in apps}
    assert 'knowledge' in by_id and 'community' in by_id

    # 字段等价：每个 app 至少含现状 manifest 的全部键（catalog 额外多带 icon_asset_uri/status，向后兼容）。
    for app in apps:
        assert _MANIFEST_KEYS <= set(app), f'缺字段: {_MANIFEST_KEYS - set(app)}'
        assert app.get('entry_route')
        assert 'status' in app

    # display 与 registry 等价。
    assert by_id['knowledge']['name'] == '知识库'
    assert by_id['knowledge']['icon'] == 'brand-knowledge'  # 应用中心改版：出厂 brand-* 品牌 token
    # launch 字段从本地 registry overlay：deck（local_tool 内联路由）的 entry_route 必须保留。
    if 'deck' in by_id:
        assert by_id['deck']['entry_route'] == '/workbench/apps/deck'
        assert by_id['deck']['execution_mode'] == 'local_tool'


async def test_disabled_catalog_app_disappears(env) -> None:
    """catalog status=disabled 的应用从列表 + 入口消失（下架即不可用）。"""
    db = env.session
    # 先经端点自播种，再把 knowledge 下架。
    _data(await env.client.get(_APPS))
    await db.execute(
        sa.update(HasnAppCatalog).where(HasnAppCatalog.app_id == 'knowledge').values(status='disabled')
    )
    await db.flush()

    apps = _data(await env.client.get(_APPS))
    assert 'knowledge' not in {a['id'] for a in apps}, '下架应用不应出现在工作台'

    # 入口同样拒绝（404，不伪造）。
    resp = await env.client.get(f'{_APPS}/knowledge/entry')
    assert resp.status_code == 404, resp.text


async def test_entry_route_from_catalog(env) -> None:
    """入口 entry_route 取自 catalog（community → /community）。"""
    handle = _data(await env.client.get(f'{_APPS}/community/entry'))
    assert handle['app_id'] == 'community'
    assert handle['entry_route'] == '/community'
    assert handle['transport'] == 'gateway_internal'
    assert 'credential' not in handle
