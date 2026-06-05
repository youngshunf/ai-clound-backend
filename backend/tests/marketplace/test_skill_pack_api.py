"""技能包 skill_pack 路由 进程内 HTTP E2E（实施/91 B2.3，真实 PG，零 mock）。

覆盖 B2 收编后的契约：
  1) POST 创建合法 skill_pack → 真落 marketplace_template(skill_pack) + version
     (bundle_slug/command_key/hermes_yaml)，hermes_yaml 是 safe_dump 规范化产出。
  2) 非法 hermes_yaml（空 skills / 顶层非 dict / slug 不自洽）→ 400 RequestError。
  3) GET 列表返回本作者私有包，他人私有不可见。

最小 app 挂真实 skill_pack 路由 + 真实 PG + override JWT（注入 request.scope['user']）；经
ASGITransport 走完整 HTTP。事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.api.v1.skill_pack import router as skill_pack_router
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_AUTHOR_ID = 970077

_APP = FastAPI()
_APP.include_router(skill_pack_router, prefix='/api/v1/marketplace/app/skill-packs')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


class _InjectUser:
    """纯 ASGI 包装：为所有请求注入 scope['user']（list 路由无鉴权依赖，靠全局中间件填 user，
    生产由 JWT 中间件设置；测试里用它替代，避免 BaseHTTPMiddleware 的跨事件循环坑）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get('type') == 'http':
            scope['user'] = SimpleNamespace(id=_AUTHOR_ID)
        await self.app(scope, receive, send)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session():
        yield session

    async def _auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=_AUTHOR_ID)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=_InjectUser(_APP)), base_url='http://e2e')
    try:
        yield SimpleNamespace(http=http, session=session)
    finally:
        await http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _payload(slug: str, **over) -> dict:
    base = {
        'namespace': 'huanxing',
        'name': slug,
        'description': 'Backend tools',
        'bundle_slug': slug,
        'command_key': f'/{slug}',
        'version': '1.0.0',
        'hermes_bundle_json': {'skills': ['developer/code-review']},
        'hermes_yaml': f'name: {slug}\ndescription: 后端开发\nskills:\n  - developer/code-review\n  - productivity/tdd\n',
        'is_private': False,
        'is_official': True,
    }
    base.update(over)
    return base


async def test_create_valid_skill_pack_persists_normalized_contract(client):
    slug = f'backend-dev-{_tag()}'
    r = await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(slug))
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['template_id'] == f'huanxing/{slug}'
    assert data['command_key'] == f'/{slug}'
    # hermes_yaml 是 safe_dump 规范化产出（含两个成员）
    assert 'developer/code-review' in data['hermes_yaml']
    assert 'productivity/tdd' in data['hermes_yaml']

    row = (
        await client.session.execute(
            text(
                '''
                SELECT t.template_type, v.bundle_slug, v.command_key, v.hermes_yaml, v.content_hash
                FROM marketplace_template t
                JOIN marketplace_template_version v ON v.template_id = t.template_id AND v.is_latest
                WHERE t.template_id = :tid
                '''
            ),
            {'tid': f'huanxing/{slug}'},
        )
    ).mappings().one()
    assert row['template_type'] == 'skill_pack'
    assert row['bundle_slug'] == slug
    assert row['command_key'] == f'/{slug}'
    assert row['content_hash'].startswith('sha256:')


async def test_create_rejects_invalid_hermes_yaml(client):
    slug = f'bad-{_tag()}'
    # 空 skills
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, hermes_yaml=f'name: {slug}\nskills: []\n'),
    )
    assert r.status_code == 400, r.text
    # slug 不自洽（command_key 不是 /slug）
    r2 = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, command_key='/wrong'),
    )
    assert r2.status_code == 400, r2.text
    # 顶层非 dict
    r3 = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, hermes_yaml='- just\n- a\n- list\n'),
    )
    assert r3.status_code == 400, r3.text


async def test_list_hides_other_owner_private_packs(client):
    slug = f'priv-{_tag()}'
    # 当前作者的私有包
    await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(slug, is_private=True, is_official=False))
    # 他人的私有包（直接落库，author_id 不同）
    other_slug = f'other-{_tag()}'
    await client.session.execute(
        text(
            '''
            INSERT INTO marketplace_template (template_id, namespace, slug, template_type, name,
                author_id, pricing_type, price, is_private, is_official, download_count, source_type,
                created_time, updated_time)
            VALUES (:tid, 'huanxing', :slug, 'skill_pack', :slug, :other, 'free', 0, true, false, 0, 'local', now(), now())
            '''
        ),
        {'tid': f'huanxing/{other_slug}', 'slug': other_slug, 'other': _AUTHOR_ID + 1},
    )
    await client.session.execute(
        text(
            '''
            INSERT INTO marketplace_template_version (template_id, version, bundle_slug, command_key,
                hermes_yaml, content_hash, file_hash, is_latest, published_at, created_time, updated_time)
            VALUES (:tid, '1.0.0', :slug, :cmd, 'name: x\nskills:\n  - a\n', 'sha256:x', 'sha256:x', true, now(), now(), now())
            '''
        ),
        {'tid': f'huanxing/{other_slug}', 'slug': other_slug, 'cmd': f'/{other_slug}'},
    )
    await client.session.flush()

    r = await client.http.get('/api/v1/marketplace/app/skill-packs')
    assert r.status_code == 200, r.text
    slugs = [item['bundle_slug'] for item in r.json()['data']]
    assert slug in slugs
    assert other_slug not in slugs  # 他人私有不可见
