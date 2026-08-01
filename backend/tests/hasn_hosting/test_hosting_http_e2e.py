"""无头节点托管三面的真实 HTTP E2E（信封 + 状态码 + 错误码）——零 mock。

service 层用例绕过 HTTP，抓不到「外壳漂移」（裸返回绕过统一信封、错误码没进 data、
中间件把内部面的服务令牌当平台 JWT 解析成 401）。这里把真实路由挂进 ASGI 走完整栈。

覆盖：
- Owner 面无 JWT → 401；
- 节点面授权码兑换三分支的 HTTP 形状（`data.error` 必须是机器可判别的码）；
- 内部面无令牌 / 错令牌 → 401，正确令牌 → 走进业务；
- 票据 → grant 的一次性（第二次 400 `access_ticket_invalid`）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn_hosting.api.router import app as hosting_app_router
from backend.app.hasn_hosting.api.router import internal as hosting_internal_router
from backend.app.hasn_hosting.api.router import node as hosting_node_router
from backend.app.hasn_hosting.constants import CODE_PURPOSE_CREATE
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.service.access_ticket_service import access_ticket_service
from backend.app.hasn_hosting.service.authorization_code_service import authorization_code_service
from backend.common.exception.exception_handler import register_exception
from backend.common.service_registry import service_endpoint
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

OWNER = 'h_hosting_http_owner'
USER_ID = 990404
NODE_ID = 'n_cloud_test_http_0001'

_APP = FastAPI()
# 统一信封的异常处理器要读 request context 拿 trace_id，缺这层中间件会整片 500
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
_APP.include_router(hosting_app_router)
_APP.include_router(hosting_node_router)
_APP.include_router(hosting_internal_router)
register_exception(_APP)


async def _purge(sess) -> None:
    await sess.execute(
        text('DELETE FROM hasn_node_authorization_codes WHERE owner_hasn_id = :o'), {'o': OWNER}
    )
    await sess.execute(text('DELETE FROM hasn_cloud_node_events WHERE node_id = :n'), {'n': NODE_ID})
    await sess.execute(text('DELETE FROM hasn_cloud_nodes WHERE node_id = :n'), {'n': NODE_ID})
    await sess.commit()


@pytest_asyncio.fixture
async def env() -> AsyncIterator:
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

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session

    await _purge(session)
    session.add(
        HasnCloudNodes(
            node_id=NODE_ID,
            user_id=USER_ID,
            owner_hasn_id=OWNER,
            host='hosting-test',
            container_ref=None,
            status='provisioning',
            failure_reason=None,
            failure_detail=None,
            image_version='0.0.1',
            image_digest='sha256:' + '3' * 64,
            credential_session_uuid=None,
            retain_until=None,
            last_backup_at=None,
            online_since=None,
        )
    )
    await session.commit()

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await _purge(session)
        await session.rollback()
        await session.close()
        await engine.dispose()


def _internal_headers() -> dict[str, str]:
    token = service_endpoint('hosting').token
    if not token:
        pytest.skip('本机未配 hosting 内部服务令牌（master_secret 为空）')
    return {'Authorization': f'Bearer {token}'}


# ── Owner 面 ──


async def test_owner_face_requires_jwt(env) -> None:
    resp = await env.client.get('/api/v1/hasn/app/cloud-nodes')
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) >= {'code', 'msg'}


# ── 节点面：三分支的 HTTP 形状 ──


async def test_exchange_unknown_code_http_shape(env) -> None:
    resp = await env.client.post(
        '/api/v1/hasn/node/cloud/authorization-code/exchange',
        json={'code': 'bogus-code-for-http-e2e', 'node_id': NODE_ID},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 400
    assert body['data'] == {'error': 'code_not_found'}


async def test_exchange_success_then_consumed_http_shape(env) -> None:
    minted = await authorization_code_service.mint(
        env.session, user_id=USER_ID, owner_hasn_id=OWNER, node_id=NODE_ID, purpose=CODE_PURPOSE_CREATE
    )
    await env.session.commit()

    ok = await env.client.post(
        '/api/v1/hasn/node/cloud/authorization-code/exchange',
        json={'code': minted.plain_code, 'node_id': NODE_ID},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body['code'] == 200
    data = body['data']
    assert data['user_id'] == USER_ID
    assert data['owner_hasn_id'] == OWNER
    assert data['node_id'] == NODE_ID
    assert data['access_token'] and data['refresh_token'] and data['expires_in'] > 0
    # 授权码明文绝不回给调用方之外的任何字段
    assert 'code' not in data and 'authorization_code' not in data

    again = await env.client.post(
        '/api/v1/hasn/node/cloud/authorization-code/exchange',
        json={'code': minted.plain_code, 'node_id': NODE_ID},
    )
    assert again.status_code == 400
    assert again.json()['data'] == {'error': 'code_consumed'}


async def test_session_grant_verify_requires_device_credential(env) -> None:
    resp = await env.client.post(
        '/api/v1/hasn/node/cloud/session-grant/verify', json={'grant': 'x' * 32}
    )
    assert resp.status_code == 401


# ── 内部面 ──


async def test_internal_face_rejects_missing_and_wrong_token(env) -> None:
    missing = await env.client.post(
        '/api/v1/hasn/internal/cloud-nodes/access-ticket/redeem', json={'ticket': 'whatever'}
    )
    assert missing.status_code == 401

    wrong = await env.client.post(
        '/api/v1/hasn/internal/cloud-nodes/access-ticket/redeem',
        headers={'Authorization': 'Bearer definitely-not-the-service-token'},
        json={'ticket': 'whatever'},
    )
    assert wrong.status_code == 401


async def test_internal_ticket_redeem_is_single_use_over_http(env) -> None:
    issued = await access_ticket_service.issue_ticket(
        user_id=USER_ID, owner_hasn_id=OWNER, node_id=NODE_ID, host='hosting-test'
    )
    headers = _internal_headers()

    first = await env.client.post(
        '/api/v1/hasn/internal/cloud-nodes/access-ticket/redeem',
        headers=headers,
        json={'ticket': issued.ticket},
    )
    assert first.status_code == 200, first.text
    data = first.json()['data']
    assert data['node_id'] == NODE_ID
    assert data['owner_hasn_id'] == OWNER
    assert data['host'] == 'hosting-test'
    assert data['grant']

    second = await env.client.post(
        '/api/v1/hasn/internal/cloud-nodes/access-ticket/redeem',
        headers=headers,
        json={'ticket': issued.ticket},
    )
    assert second.status_code == 400
    assert second.json()['data'] == {'error': 'access_ticket_invalid'}


async def test_internal_status_report_rejects_online(env) -> None:
    resp = await env.client.post(
        f'/api/v1/hasn/internal/cloud-nodes/{NODE_ID}/status',
        headers=_internal_headers(),
        json={'status': 'online', 'host': 'hosting-test'},
    )
    assert resp.status_code == 400, resp.text


async def test_internal_status_report_persists_host(env) -> None:
    resp = await env.client.post(
        f'/api/v1/hasn/internal/cloud-nodes/{NODE_ID}/status',
        headers=_internal_headers(),
        json={'status': 'starting', 'host': 'hosting-09', 'container_ref': 'c-9'},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()['data']['status'] == 'starting'

    row = (
        await env.session.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == NODE_ID))
    ).scalar_one()
    await env.session.refresh(row)
    assert row.host == 'hosting-09'


async def test_internal_pending_updates_envelope(env) -> None:
    resp = await env.client.get(
        '/api/v1/hasn/internal/cloud-nodes/pending-updates', headers=_internal_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    assert 'image_available' in body['data']
    assert isinstance(body['data']['nodes'], list)
