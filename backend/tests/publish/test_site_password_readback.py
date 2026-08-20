"""Publish 站点口令明文回读 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖 2026-08-19「主人可见口令 + 复制带口令链接」裁决的云端面：
  - create/set_visibility 写入 password_plain；owner/app 通道 detail/list 回读 `password`
  - 离开 password 档（改 public/private/unlisted）→ hash 与明文一起清空，回读为 None
  - password 档内换口令 → 明文同步更新
  - open meta 访客面绝不泄露 password / password_plain / password_hash
  - site_to_dict 防御门：visibility != password 时即使明文列有值也回 None

事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

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
from backend.app.hasn_publish.model.site import Site
from backend.app.hasn_publish.service.publish_service import publish_service, site_to_dict
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

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
    owner = f'h_pr_{tag}'
    owner_uid = 9_700_000_000 + int(uuid.uuid4().int % 1_000_000_000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='P', status='active'))
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_APP),
        base_url='http://pr.test',
        headers={'x-forwarded-for': f'2001:db8:1::{tag}'},
    )
    try:
        yield SimpleNamespace(client=client, session=session, owner=owner)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _make_password_site(ws: SimpleNamespace, password: str = 'letmein') -> dict[str, Any]:
    data = await publish_service.create_site(
        ws.session,
        owner_id=ws.owner,
        kind='page',
        title='口令回读测试',
        asset_id=f'ast_{_uid()}',
        content_hash=_uid(),
        size_bytes=64,
        visibility='password',
        password=password,
    )
    await ws.session.flush()
    return data['site']


async def test_owner_detail_and_list_read_back_password(ws: SimpleNamespace) -> None:
    site = await _make_password_site(ws, 'pw-abc-123')
    r = await ws.client.get(f'/api/v1/publish/app/sites/{site["id"]}')
    assert r.status_code == 200, r.text
    detail = r.json()['data']['site']
    assert detail['password'] == 'pw-abc-123'
    assert detail['has_password'] is True
    assert 'password_hash' not in detail
    assert 'password_plain' not in detail

    r = await ws.client.get('/api/v1/publish/app/sites')
    assert r.status_code == 200, r.text
    items = r.json()['data']['items']
    mine = next(i for i in items if i['id'] == site['id'])
    assert mine['password'] == 'pw-abc-123'


async def test_meta_never_leaks_password(ws: SimpleNamespace) -> None:
    site = await _make_password_site(ws)
    r = await ws.client.get(f'/api/v1/publish/open/sites/{site["slug"]}/meta')
    assert r.status_code == 200, r.text
    d = r.json()['data']
    assert d['has_password'] is True
    assert 'password' not in d
    assert 'password_plain' not in d
    assert 'password_hash' not in d


async def test_leaving_password_clears_plaintext(ws: SimpleNamespace) -> None:
    site = await _make_password_site(ws)
    r = await ws.client.patch(
        f'/api/v1/publish/app/sites/{site["id"]}/visibility',
        json={'visibility': 'public'},
    )
    assert r.status_code == 200, r.text
    changed = r.json()['data']['site']
    assert changed['visibility'] == 'public'
    assert changed['password'] is None
    assert changed['has_password'] is False


async def test_change_password_within_password_visibility(ws: SimpleNamespace) -> None:
    site = await _make_password_site(ws, 'old-pw')
    r = await ws.client.patch(
        f'/api/v1/publish/app/sites/{site["id"]}/visibility',
        json={'visibility': 'password', 'password': 'new-pw'},
    )
    assert r.status_code == 200, r.text
    changed = r.json()['data']['site']
    assert changed['password'] == 'new-pw'
    # 旧口令失效、新口令可解锁（hash 校验路径不变）
    row = await ws.session.get(Site, site['id'])
    assert row is not None
    assert await publish_service.verify_unlock(ws.session, site=row, password='old-pw') is False
    assert await publish_service.verify_unlock(ws.session, site=row, password='new-pw') is True


async def test_site_to_dict_gates_plaintext_on_visibility() -> None:
    """防御门：非 password 档即使明文列残留也绝不回读（不依赖 DB）。"""
    site = Site(
        owner_id='h_x',
        kind='page',
        title='t',
        slug='slug12345678',
        visibility='public',
        password_hash='hashed',
        password_plain='should-not-leak',
    )
    assert site_to_dict(site)['password'] is None
    site.visibility = 'password'
    assert site_to_dict(site)['password'] == 'should-not-leak'
