"""B1 - GET /api/v1/app/owner_api_keys/current 端点契约测试.

测试策略: 搭建一个最小 FastAPI 测试 app, 仅挂载被测 router, 通过
`app.dependency_overrides` 替换 JWT 依赖 + CurrentSessionTransaction,
同时 monkeypatch `get_current_owner_api_key_for_user` 业务函数以解耦数据库。

本测试不依赖 pytest-asyncio / aiosqlite / Postgres / Redis, 只需 fastapi + httpx
(starlette.testclient), 与当前 `.venv` 可用依赖一致。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.status import HTTP_401_UNAUTHORIZED

from backend.app.hasn_client.api import owner_api_keys as owner_module
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db_transaction

FAKE_USER_ID = 42
FAKE_HASN_ID = 'h_100001'
FAKE_EXPIRES_AT = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


async def _fake_db_transaction():
    """替身 DB 依赖: 端点里 db 不会被真正调用 (service 已 monkeypatch)."""
    yield SimpleNamespace()


async def _fake_jwt_auth_ok(request: Request):
    """模拟 JWT 认证: 有 Bearer → 注入 request.user; 缺失 → 401."""
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    fake_user = SimpleNamespace(id=FAKE_USER_ID, username='tester')
    request.scope['user'] = fake_user
    request.scope['auth'] = ['authenticated']
    return auth[7:]


@pytest.fixture
def test_app(monkeypatch):
    """最小测试 app, 仅含 /api/v1/app/owner_api_keys/* 路由."""
    app = FastAPI()
    app.include_router(owner_module.router, prefix='/api/v1/app/owner_api_keys')

    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_ok
    app.dependency_overrides[get_db_transaction] = _fake_db_transaction

    async def fake_service(db, user_id: int):
        assert user_id == FAKE_USER_ID
        return owner_module._CurrentKeyResult(
            owner_api_key='hasn_ok_test_' + 'A' * 48,
            hasn_id=FAKE_HASN_ID,
            expires_at=FAKE_EXPIRES_AT,
        )

    monkeypatch.setattr(
        owner_module,
        'get_current_owner_api_key_for_user',
        fake_service,
    )

    return app


def test_get_current_owner_api_key_returns_200_when_authenticated(test_app) -> None:
    """登录 (携带 Bearer) → GET → 200 + body 含 owner_api_key 字段."""
    with TestClient(test_app) as client:
        resp = client.get(
            '/api/v1/app/owner_api_keys/current',
            headers={'Authorization': 'Bearer fake-jwt-token'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    data = body['data']
    assert 'owner_api_key' in data, 'body 必须包含 owner_api_key 字段'
    assert data['owner_api_key'].startswith('hasn_ok_'), (
        'owner_api_key 明文应以 hasn_ok_ 前缀 (05 §6.5)'
    )
    assert data['hasn_id'] == FAKE_HASN_ID
    assert data['expires_at'] is not None


def test_get_current_owner_api_key_returns_401_when_unauthenticated(test_app) -> None:
    """未登录 (缺失 Authorization) → 401."""
    with TestClient(test_app) as client:
        resp = client.get('/api/v1/app/owner_api_keys/current')

    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text
