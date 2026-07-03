"""B2 - POST /api/v1/auth/logout + JWT 吊销表契约测试.

测试策略 (与 B1 `test_owner_api_keys.py` 一致):
- 搭建最小 FastAPI 测试 app, 仅挂载被测 router + 一个受
  `mobile_jwt_auth_with_revocation` 保护的 dummy 端点。
- `app.dependency_overrides` 替换 `DependsJwtAuth` + `get_db_transaction`。
- `monkeypatch` 把 `jwt_decode` / `revoke_jwt` / `is_jwt_revoked` 换成 in-memory stub。
- 不依赖真实 Postgres / Redis / pytest-asyncio / aiosqlite。

覆盖 B2 acceptance:
1. 受保护端点在登录态下 200
2. POST /logout → 200 + in-memory jwt_revocations 多一条 (jti = session_uuid)
3. 登出后再用同 JWT 调任意受保护端点 → 401
4. 重复 logout 幂等
5. 未携带 Authorization → 401
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.status import HTTP_200_OK, HTTP_401_UNAUTHORIZED

from backend.app.api.v1.auth import jwt_revocation as jwt_revocation_module
from backend.app.api.v1.auth import logout as logout_module
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db_transaction

FAKE_USER_ID = 42
FAKE_SESSION_UUID = 'sess-b2-42-abcdef'
FAKE_TOKEN = 'fake-jwt-token-for-b2'


async def _fake_db_transaction():
    """替身 DB 依赖: 只产出 placeholder, service 层 DB 交互都已 monkeypatch 掉."""
    yield SimpleNamespace()


async def _fake_jwt_auth_ok(request: Request):
    """模拟 JWT 认证: 有 Bearer → 放行; 缺失 → 401."""
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    request.scope['user'] = SimpleNamespace(id=FAKE_USER_ID, username='tester')
    request.scope['auth'] = ['authenticated']
    return auth[7:]


@pytest.fixture
def test_env(monkeypatch):
    """最小测试 app + in-memory jwt_revocations + stubbed jwt_decode."""
    revocations: dict[str, dict] = {}

    def fake_jwt_decode(token: str):
        # 只要 token == FAKE_TOKEN 就返回固定 payload; 其他 token 抛 TokenError
        if token != FAKE_TOKEN:
            from backend.common.exception import errors

            raise errors.TokenError(msg='Token 无效')
        return SimpleNamespace(
            id=FAKE_USER_ID,
            session_uuid=FAKE_SESSION_UUID,
            expire_time=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        )

    async def fake_revoke_jwt(db, *, jti, user_id, expires_at=None) -> None:
        # 幂等: 已在就直接返回 (与真实实现一致)
        if jti in revocations:
            return
        revocations[jti] = {
            'user_id': user_id,
            'revoked_at': datetime.now(tz=timezone.utc),
            'expires_at': expires_at,
        }

    async def fake_is_jwt_revoked(db, *, jti):
        return jti in revocations

    # logout.py 内 `from ... import jwt_revocation as jwt_revocation_module` + 属性访问,
    # 所以改 module 的 attribute 即可生效; jwt_decode 以 `from backend.common.security.jwt
    # import jwt_decode` 导入了, 要打在 logout_module 本地绑定上。
    monkeypatch.setattr(logout_module, 'jwt_decode', fake_jwt_decode)
    monkeypatch.setattr(jwt_revocation_module, 'revoke_jwt', fake_revoke_jwt)
    monkeypatch.setattr(jwt_revocation_module, 'is_jwt_revoked', fake_is_jwt_revoked)

    app = FastAPI()
    app.include_router(logout_module.router, prefix='/api/v1/auth')

    @app.get('/api/v1/app/dummy')
    async def _dummy(_=Depends(logout_module.mobile_jwt_auth_with_revocation)):
        return {'ok': True}

    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_ok
    app.dependency_overrides[get_db_transaction] = _fake_db_transaction

    return SimpleNamespace(app=app, revocations=revocations)


def test_dummy_endpoint_passes_when_not_revoked(test_env) -> None:
    """登录态 + 未吊销 → 受保护端点 200."""
    with TestClient(test_env.app) as client:
        resp = client.get(
            '/api/v1/app/dummy',
            headers={'Authorization': f'Bearer {FAKE_TOKEN}'},
        )
    assert resp.status_code == HTTP_200_OK, resp.text
    assert resp.json() == {'ok': True}


def test_logout_writes_revocation_record(test_env) -> None:
    """POST /api/v1/auth/logout → 200 + in-memory revocations 多一条."""
    with TestClient(test_env.app) as client:
        resp = client.post(
            '/api/v1/auth/logout',
            headers={'Authorization': f'Bearer {FAKE_TOKEN}'},
        )
    assert resp.status_code == HTTP_200_OK, resp.text
    body = resp.json()
    assert body['code'] == 200, body

    assert FAKE_SESSION_UUID in test_env.revocations, (
        'logout 必须把 session_uuid 作为 jti 写入 jwt_revocations'
    )
    row = test_env.revocations[FAKE_SESSION_UUID]
    assert row['user_id'] == FAKE_USER_ID
    assert row['revoked_at'] is not None


def test_logout_then_same_jwt_returns_401(test_env) -> None:
    """登录 → logout → 用同一 JWT 调任意受保护端点 → 401 (acceptance 核心断言)."""
    with TestClient(test_env.app) as client:
        logout_resp = client.post(
            '/api/v1/auth/logout',
            headers={'Authorization': f'Bearer {FAKE_TOKEN}'},
        )
        assert logout_resp.status_code == HTTP_200_OK, logout_resp.text

        probe_resp = client.get(
            '/api/v1/app/dummy',
            headers={'Authorization': f'Bearer {FAKE_TOKEN}'},
        )
    assert probe_resp.status_code == HTTP_401_UNAUTHORIZED, probe_resp.text


def test_logout_is_idempotent(test_env) -> None:
    """重复 logout 不报错, 吊销表仅一条记录."""
    with TestClient(test_env.app) as client:
        r1 = client.post('/api/v1/auth/logout', headers={'Authorization': f'Bearer {FAKE_TOKEN}'})
        r2 = client.post('/api/v1/auth/logout', headers={'Authorization': f'Bearer {FAKE_TOKEN}'})
    assert r1.status_code == HTTP_200_OK
    assert r2.status_code == HTTP_200_OK
    assert len(test_env.revocations) == 1


def test_logout_without_bearer_returns_401(test_env) -> None:
    """未携带 Authorization → 401 (JWT 中间件直接拒)."""
    with TestClient(test_env.app) as client:
        resp = client.post('/api/v1/auth/logout')
    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text
