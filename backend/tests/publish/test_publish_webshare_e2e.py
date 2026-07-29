"""通用网页发布与分享（模块 18，WEBSHARE-B）website 查看器接入端点 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖 WEBSHARE-B 两个新端点（website `/s/{slug}` SPA 查看器调用）：
  - GET /api/v1/publish/open/sites/{slug}/meta —— 匿名判定渲染态（四态 + 过期/撤销/不存在）
      · public   → available=True，requires_login=False，has_password=False
      · unlisted → available=True，requires_login=False（凭链接可见）
      · password → has_password=True，requires_login=False（口令 ≠ 登录）
      · private  → requires_login=True（查看器引导 owner 登录换票）
      · 不存在/撤销 → 404（探测限速防枚举）；过期/无当前版本 → 200 但 available=False（诚实空态）
      · meta 不泄露 owner_id / asset_id / password_hash
  - POST /api/v1/publish/app/sites/by-slug/{slug}/view-ticket —— owner 登录后按 slug 换 private 访问票
      · owner 本人 → 200 + 绑定 site_id 的有效票
      · 非 owner → 404（不泄露他人 slug 归属，防 owner 枚举）
      · 已撤销 → 404

同挂 meta（匿名）+ app（owner JWT override）路由，经 ASGITransport 走完整 HTTP；事务末尾回滚不污染库。
需要 export DATABASE_PORT=15432。
设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/实施/03-分享查看器迁移website与官网重新定位.md（阶段 B）。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_publish.api.v1.app.site import router as app_site_router
from backend.app.hasn_publish.api.v1.open.meta import router as meta_open_router
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(meta_open_router, prefix='/api/v1/publish/open')
_APP.include_router(app_site_router, prefix='/api/v1/publish/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def ws() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = _uid()
    owner = f'h_ws_{tag}'
    other = f'h_ws2_{tag}'
    owner_uid = 9_600_000_000 + int(uuid.uuid4().int % 1_000_000_000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='W', status='active'))
    session.add(
        HasnHumans(hasn_id=other, star_id=f's_{owner_uid + 1}', user_id=owner_uid + 1, nickname='W2', status='active')
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_APP),
        base_url='http://ws.test',
        headers={'x-forwarded-for': f'2001:db8::{tag}'},
    )
    try:
        yield SimpleNamespace(client=client, session=session, owner=owner, other=other)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _make_site(
    ws: SimpleNamespace, *, owner_id: str | None = None, visibility: str, password: str | None = None, **extra: Any
) -> dict:
    data = await publish_service.create_site(
        ws.session,
        owner_id=owner_id or ws.owner,
        kind='page',
        title='分享测试',
        asset_id=f'ast_{_uid()}',
        content_hash=_uid(),
        size_bytes=64,
        visibility=visibility,
        password=password,
        **extra,
    )
    await ws.session.flush()
    return data['site']


async def _meta(ws: SimpleNamespace, slug: str) -> httpx.Response:
    return await ws.client.get(f'/api/v1/publish/open/sites/{slug}/meta')


# ---------- meta 四态 + 边界 ----------


async def test_meta_public(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='public')
    r = await _meta(ws, site['slug'])
    assert r.status_code == 200, r.text
    d = r.json()['data']
    assert d['visibility'] == 'public'
    assert d['available'] is True
    assert d['requires_login'] is False
    assert d['has_password'] is False
    assert d['slug'] == site['slug']
    # meta 绝不泄露敏感字段
    assert 'owner_id' not in d
    assert 'asset_id' not in d
    assert 'password_hash' not in d


async def test_meta_unlisted(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='unlisted')
    d = (await _meta(ws, site['slug'])).json()['data']
    assert d['visibility'] == 'unlisted'
    assert d['available'] is True
    assert d['requires_login'] is False


async def test_meta_password(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='password', password='letmein')
    d = (await _meta(ws, site['slug'])).json()['data']
    assert d['visibility'] == 'password'
    assert d['has_password'] is True
    assert d['requires_login'] is False  # 口令 ≠ 登录
    assert d['available'] is True


async def test_meta_private_requires_login(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='private')
    d = (await _meta(ws, site['slug'])).json()['data']
    assert d['visibility'] == 'private'
    assert d['requires_login'] is True  # 查看器引导 owner 登录换票
    assert d['has_password'] is False


async def test_meta_not_found(ws: SimpleNamespace) -> None:
    r = await _meta(ws, 'doesnotexist999')
    assert r.status_code == 404, r.text


async def test_meta_revoked_is_404(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='public')
    await publish_service.revoke(ws.session, owner_id=ws.owner, site_id=site['id'])
    await ws.session.flush()
    r = await _meta(ws, site['slug'])
    assert r.status_code == 404, r.text


async def test_meta_expired_is_available_false(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='public', expires_at=datetime.now(UTC) - timedelta(hours=1))
    r = await _meta(ws, site['slug'])
    assert r.status_code == 200, r.text  # 过期不是 404（区别于撤销/不存在）
    d = r.json()['data']
    assert d['expired'] is True
    assert d['available'] is False  # 查看器出诚实空态


# ---------- by-slug 换票（owner 隔离） ----------


async def test_by_slug_ticket_owner_pass(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='private')
    r = await ws.client.post(f'/api/v1/publish/app/sites/by-slug/{site["slug"]}/view-ticket')
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['ttl_seconds'] == 600
    # 票绑定该 site_id
    assert publish_service.verify_view_ticket(data['ticket'], site_id=site['id']) is True
    assert publish_service.verify_view_ticket(data['ticket'], site_id=site['id'] + 1) is False


async def test_by_slug_ticket_non_owner_is_404(ws: SimpleNamespace) -> None:
    # 站点归 other，登录身份是 owner → 按 slug 换票恒 404（不泄露归属）
    site = await _make_site(ws, owner_id=ws.other, visibility='private')
    r = await ws.client.post(f'/api/v1/publish/app/sites/by-slug/{site["slug"]}/view-ticket')
    assert r.status_code == 404, r.text


async def test_by_slug_ticket_revoked_is_404(ws: SimpleNamespace) -> None:
    site = await _make_site(ws, visibility='private')
    await publish_service.revoke(ws.session, owner_id=ws.owner, site_id=site['id'])
    await ws.session.flush()
    r = await ws.client.post(f'/api/v1/publish/app/sites/by-slug/{site["slug"]}/view-ticket')
    assert r.status_code == 404, r.text


async def test_by_slug_ticket_not_found_is_404(ws: SimpleNamespace) -> None:
    r = await ws.client.post('/api/v1/publish/app/sites/by-slug/nope12345/view-ticket')
    assert r.status_code == 404, r.text
