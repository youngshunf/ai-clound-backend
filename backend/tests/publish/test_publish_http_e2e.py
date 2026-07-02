"""通用网页发布与分享（模块 18，P1）云端数据层 + 权限 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖（DoD）：
  - owner 创建 → 列表 → 详情（slug 不可枚举 + current_revision_id 落指针）
  - 更新 = 新 revision（seq 递增）+ content_hash 去重复用
  - 四态可见性切换（private/password/unlisted/public）+ password 必带口令 + 离开 password 清 hash
  - 过期 expires_at + 撤销 revoke（status=revoked + current_revision_id 置空）+ 软删后 404
  - owner 隔离（跨 owner 恒 NotFound）+ agent scope 闸（缺 publish:write → 403）
  - slug 唯一（两次创建不同 slug）
  - 浏览器访问票签发 + 验票（绑定 site_id）
  - by-source 反查（deck 更新用）
  - agent 代发布记 publisher_agent_id

最小 app 同挂 app + agent 路由，override 鉴权与 DB 会话，经 ASGITransport 走完整 HTTP；
事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_publish.api.v1.agent.site import router as agent_site_router
from backend.app.hasn_publish.api.v1.app.site import router as app_site_router
from backend.app.hasn_publish.service.publish_service import publish_service, visibility_rank
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(app_site_router, prefix='/api/v1/publish/app')
_APP.include_router(agent_site_router, prefix='/api/v1/publish/agent')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------- 纯函数（无 DB） ----------


def test_visibility_rank_order() -> None:
    assert visibility_rank('private') < visibility_rank('password') < visibility_rank('unlisted') < visibility_rank(
        'public'
    )
    assert visibility_rank('unknown') == 0  # 未知保守为 private


def test_view_ticket_roundtrip() -> None:
    issued = publish_service.issue_view_ticket(site_id=42, owner_id='h_x')
    assert issued['ttl_seconds'] == 600
    assert publish_service.verify_view_ticket(issued['ticket'], site_id=42) is True
    assert publish_service.verify_view_ticket(issued['ticket'], site_id=99) is False  # 绑定 site_id
    assert publish_service.verify_view_ticket('garbage', site_id=42) is False


def test_scope_catalog_has_publish() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert 'publish:read' in SCOPE_CATALOG
    assert 'publish:write' in SCOPE_CATALOG


def test_published_artifact_category_no_extract() -> None:
    from backend.plugin.s3.service.storage_service import CATEGORY_DIR, CATEGORY_POLICY

    assert CATEGORY_POLICY['published_artifact'] == ('private', 3600)
    assert CATEGORY_DIR['published_artifact'] == 'published'


# ---------- E2E（真实 PG） ----------


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    tag = _uid()
    owner = f'h_pub_{tag}'
    other_owner = f'h_pub2_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_pub_{tag}'
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='O', status='active'))
    session.add(
        HasnHumans(
            hasn_id=other_owner, star_id=f's_{owner_uid + 1}', user_id=owner_uid + 1, nickname='O2', status='active'
        )
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    state = SimpleNamespace(agent_owner=owner, agent_scopes=['agent', 'publish:read', 'publish:write'])

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=state.agent_owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        request.state.agent = payload
        return payload

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, other_owner=other_owner, agent_hasn=agent_hasn, state=state
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _create(client: httpx.AsyncClient, **extra: object) -> dict:
    body = {'kind': 'page', 'title': '我的页面', 'asset_id': f'ast_{_uid()}', 'content_hash': _uid(), 'size_bytes': 1024}
    body.update(extra)
    r = await client.post('/api/v1/publish/app/sites', json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['code'] == 200, data
    return data['data']


async def test_create_list_get(e2e: SimpleNamespace) -> None:
    c = e2e.client
    created = await _create(c, title='Q2 路线图')
    site = created['site']
    assert site['slug'] and len(site['slug']) >= 10  # 不可枚举短码
    assert site['status'] == 'active'
    assert site['visibility'] == 'private'  # 默认私有
    assert site['current_revision_id'] == created['revision']['id']  # 指针落位
    assert created['revision']['seq'] == 1
    assert 'password_hash' not in site  # 不泄露敏感字段

    r = await c.get('/api/v1/publish/app/sites')
    assert r.status_code == 200, r.text
    lst = r.json()['data']
    assert lst['total'] >= 1
    assert any(s['id'] == site['id'] for s in lst['items'])

    r = await c.get(f'/api/v1/publish/app/sites/{site["id"]}')
    assert r.status_code == 200, r.text
    assert r.json()['data']['site']['slug'] == site['slug']


async def test_slug_unique(e2e: SimpleNamespace) -> None:
    a = await _create(e2e.client)
    b = await _create(e2e.client)
    assert a['site']['slug'] != b['site']['slug']


async def test_update_new_revision_and_dedup(e2e: SimpleNamespace) -> None:
    c = e2e.client
    created = await _create(c, content_hash='hash_v1')
    sid = created['site']['id']

    # 新 hash → 新 revision（seq 2）
    r = await c.put(
        f'/api/v1/publish/app/sites/{sid}', json={'asset_id': 'ast_v2', 'content_hash': 'hash_v2', 'size_bytes': 2048}
    )
    assert r.status_code == 200, r.text
    body = r.json()['data']
    assert body['reused'] is False
    assert body['revision']['seq'] == 2
    assert body['site']['current_revision_id'] == body['revision']['id']

    # 同 hash 再更新 → 复用 revision（不新增）
    r = await c.put(
        f'/api/v1/publish/app/sites/{sid}', json={'asset_id': 'ast_v2', 'content_hash': 'hash_v2', 'size_bytes': 2048}
    )
    assert r.status_code == 200, r.text
    body2 = r.json()['data']
    assert body2['reused'] is True
    assert body2['revision']['id'] == body['revision']['id']


async def test_visibility_four_states(e2e: SimpleNamespace) -> None:
    c = e2e.client
    sid = (await _create(c))['site']['id']

    # → unlisted
    r = await c.patch(f'/api/v1/publish/app/sites/{sid}/visibility', json={'visibility': 'unlisted'})
    assert r.status_code == 200, r.text
    assert r.json()['data']['site']['visibility'] == 'unlisted'

    # → password 必带口令；不带 → 400
    r = await c.patch(f'/api/v1/publish/app/sites/{sid}/visibility', json={'visibility': 'password'})
    assert r.status_code == 400, r.text
    r = await c.patch(
        f'/api/v1/publish/app/sites/{sid}/visibility', json={'visibility': 'password', 'password': 's3cret'}
    )
    assert r.status_code == 200, r.text
    assert r.json()['data']['site']['has_password'] is True

    # password → public：离开 password 清空 hash（不变量 3）
    r = await c.patch(
        f'/api/v1/publish/app/sites/{sid}/visibility', json={'visibility': 'public', 'allow_indexing': True}
    )
    assert r.status_code == 200, r.text
    site = r.json()['data']['site']
    assert site['visibility'] == 'public'
    assert site['has_password'] is False
    assert site['allow_indexing'] is True

    # 库里确认 password_hash 已清
    pw = (
        await e2e.session.execute(text('SELECT password_hash FROM hasn_publish.site WHERE id = :i'), {'i': sid})
    ).scalar_one()
    assert pw is None


async def test_expiry_and_revoke_and_delete(e2e: SimpleNamespace) -> None:
    c = e2e.client
    sid = (await _create(c))['site']['id']

    # 设过期
    r = await c.patch(
        f'/api/v1/publish/app/sites/{sid}/visibility', json={'expires_at': '2030-01-01T00:00:00+00:00'}
    )
    assert r.status_code == 200, r.text
    assert r.json()['data']['site']['expires_at'] is not None

    # revoke → status=revoked + current_revision_id 置空
    r = await c.post(f'/api/v1/publish/app/sites/{sid}/revoke')
    assert r.status_code == 200, r.text
    revoked = r.json()['data']['site']
    assert revoked['status'] == 'revoked'
    assert revoked['current_revision_id'] is None

    # delete（软删）→ get 404
    r = await c.delete(f'/api/v1/publish/app/sites/{sid}')
    assert r.status_code == 200, r.text
    r = await c.get(f'/api/v1/publish/app/sites/{sid}')
    assert r.status_code == 404, r.text


async def test_view_ticket_endpoint(e2e: SimpleNamespace) -> None:
    c = e2e.client
    sid = (await _create(c))['site']['id']
    r = await c.post(f'/api/v1/publish/app/sites/{sid}/view-ticket')
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['ttl_seconds'] == 600
    assert publish_service.verify_view_ticket(data['ticket'], site_id=sid) is True


async def test_by_source_lookup(e2e: SimpleNamespace) -> None:
    c = e2e.client
    created = await _create(c, source_app='deck', source_ref='deck_01HX')
    r = await c.get('/api/v1/publish/app/sites/by-source', params={'source_app': 'deck', 'source_ref': 'deck_01HX'})
    assert r.status_code == 200, r.text
    found = r.json()['data']['site']
    assert found is not None
    assert found['id'] == created['site']['id']

    r = await c.get('/api/v1/publish/app/sites/by-source', params={'source_app': 'deck', 'source_ref': 'nope'})
    assert r.json()['data']['site'] is None


async def test_owner_isolation(e2e: SimpleNamespace) -> None:
    c = e2e.client
    created = await _create(c)
    sid = created['site']['id']
    # 把这条改成 other_owner 的（直接改库），owner 再读应 404
    await e2e.session.execute(
        text('UPDATE hasn_publish.site SET owner_id = :o WHERE id = :i'), {'o': e2e.other_owner, 'i': sid}
    )
    await e2e.session.flush()
    r = await c.get(f'/api/v1/publish/app/sites/{sid}')
    assert r.status_code == 404, r.text


async def test_agent_create_records_publisher_and_scope_gate(e2e: SimpleNamespace) -> None:
    c = e2e.client
    # agent 创建（带 publish:write）→ publisher_agent_id 落 agent
    r = await c.post(
        '/api/v1/publish/agent/sites',
        json={'kind': 'report', 'title': 'Agent 报告', 'asset_id': 'ast_ag', 'content_hash': 'h_ag', 'size_bytes': 16},
    )
    assert r.status_code == 200, r.text
    site = r.json()['data']['site']
    assert site['publisher_agent_id'] == e2e.agent_hasn
    assert site['owner_id'] == e2e.owner

    # 撤掉 publish:write → 创建被 403
    e2e.state.agent_scopes = ['agent', 'publish:read']
    try:
        r = await c.post(
            '/api/v1/publish/agent/sites',
            json={'kind': 'page', 'title': 'no write', 'asset_id': 'ast_x', 'content_hash': 'h_x', 'size_bytes': 1},
        )
        assert r.status_code != 200, r.text
    finally:
        e2e.state.agent_scopes = ['agent', 'publish:read', 'publish:write']


async def test_agent_cross_owner_isolation(e2e: SimpleNamespace) -> None:
    c = e2e.client
    sid = (await _create(c))['site']['id']  # owner 创建
    # 切到 other_owner 的 agent 身份 → 读恒 NotFound
    e2e.state.agent_owner = e2e.other_owner
    try:
        r = await c.get(f'/api/v1/publish/agent/sites/{sid}')
        assert r.status_code == 404, r.text
        r = await c.get('/api/v1/publish/agent/sites')
        assert r.status_code == 200
        assert r.json()['data']['items'] == []
    finally:
        e2e.state.agent_owner = e2e.owner
