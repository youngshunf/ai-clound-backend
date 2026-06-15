"""B4 - POST / DELETE /api/v1/app/push_tokens 端点契约测试.

测试策略 (与 B1 test_owner_api_keys.py / B2 test_logout.py 一致):
- 最小 FastAPI 测试 app, 只挂被测 router, 通过 `app.dependency_overrides`
  替换 JWT 依赖 + CurrentSessionTransaction。
- monkeypatch 业务函数 `upsert_push_token_for_user` /
  `delete_push_tokens_by_device_for_user` 以解耦 DB (避免 aiosqlite 依赖,
  与 B3 模型层契约测试互补: 一层测 DDL / 唯一约束, 这里测路由 + 契约 + 鉴权)。

覆盖 B4 acceptance:
1. POST + DELETE 两个端点都挂在 router 上 (路径 + 方法契约)
2. POST 正常路径 → 200 + body 含 device_id / channel / last_seen_at
3. POST 同 device_id 重复 → upsert: 第二次调用 service 收到新 token + 相同 device_id
4. POST 未登录 → 401
5. POST channel 非法 → 400 (RequestError)
6. DELETE 按 device_id 级联清除 → 调用业务函数时传入完整 device_id (全 channel)
7. DELETE 未登录 → 401
"""
from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED

from backend.app.hasn_client.api import push_tokens as push_tokens_module
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

FAKE_USER_ID = 42
FAKE_HASN_ID = 'h_100001'
FAKE_DEVICE_ID = 'dev-synthetic-001'
FAKE_TOKEN = 'umeng_tok_' + 'A' * 40
FAKE_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)


def _fake_db_transaction() -> Generator[SimpleNamespace, None, None]:
    """替身 DB 依赖 (sync generator, FastAPI 接受)."""
    yield SimpleNamespace()


def _fake_jwt_auth_ok(request: Request) -> str:
    """模拟 JWT: 有 Bearer → 注入 user; 缺失 → 401 (与 B1 同模式)."""
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    fake_user = SimpleNamespace(id=FAKE_USER_ID, username='tester')
    request.scope['user'] = fake_user
    request.scope['auth'] = ['authenticated']
    return auth[7:]


@pytest.fixture
def test_app() -> FastAPI:
    """最小测试 app, 仅 /api/v1/app/push_tokens/* 路由."""
    app = FastAPI()
    app.include_router(push_tokens_module.router, prefix='/api/v1/app/push_tokens')

    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_ok
    app.dependency_overrides[get_db_transaction] = _fake_db_transaction

    return app


def _patch_upsert(monkeypatch: pytest.MonkeyPatch, recorder: list[dict[str, Any]]) -> None:
    async def fake_upsert(  # noqa: RUF029 — must be async to match awaited signature
        db: object, *, user_id: int, device_id: str, channel: str, token: str,
    ) -> push_tokens_module._UpsertResult:
        recorder.append({
            'user_id': user_id,
            'device_id': device_id,
            'channel': channel,
            'token': token,
        })
        return push_tokens_module._UpsertResult(
            device_id=device_id,
            channel=channel,
            registered_at=FAKE_NOW,
            last_seen_at=FAKE_NOW,
        )

    monkeypatch.setattr(
        push_tokens_module, 'upsert_push_token_for_user', fake_upsert
    )


def _patch_delete(
    monkeypatch: pytest.MonkeyPatch,
    recorder: list[dict[str, Any]],
    rowcount: int = 2,
) -> None:
    async def fake_delete(  # noqa: RUF029 — must be async to match awaited signature
        db: object, *, user_id: int, device_id: str,
    ) -> int:
        recorder.append({'user_id': user_id, 'device_id': device_id})
        return rowcount

    monkeypatch.setattr(
        push_tokens_module,
        'delete_push_tokens_by_device_for_user',
        fake_delete,
    )


# ---------------------------------------------------------------------------
# 路由挂载契约
# ---------------------------------------------------------------------------


def test_router_exposes_post_root_and_delete_device_path(test_app: FastAPI) -> None:
    paths_methods = {
        (route.path, tuple(sorted(route.methods)))
        for route in test_app.routes
        if hasattr(route, 'methods') and getattr(route, 'methods', None)
    }
    assert ('/api/v1/app/push_tokens', ('POST',)) in paths_methods, (
        f'POST /api/v1/app/push_tokens 路由缺失; 实际={paths_methods}'
    )
    assert (
        '/api/v1/app/push_tokens/{device_id}', ('DELETE',)
    ) in paths_methods, (
        f'DELETE /api/v1/app/push_tokens/{{device_id}} 路由缺失; 实际={paths_methods}'
    )


# ---------------------------------------------------------------------------
# POST: 正常注册
# ---------------------------------------------------------------------------


def test_post_register_returns_200_and_calls_upsert(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _patch_upsert(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_tokens',
            json={
                'device_id': FAKE_DEVICE_ID,
                'channel': 'umeng_push',
                'token': FAKE_TOKEN,
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    data = body['data']
    assert data['device_id'] == FAKE_DEVICE_ID
    assert data['channel'] == 'umeng_push'
    assert 'registered_at' in data and 'last_seen_at' in data

    assert len(calls) == 1
    assert calls[0]['user_id'] == FAKE_USER_ID
    assert calls[0]['device_id'] == FAKE_DEVICE_ID
    assert calls[0]['token'] == FAKE_TOKEN


def test_post_register_defaults_channel_to_umeng_push(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 channel 字段 → 默认 'umeng_push' (M1 唯一 channel)."""
    calls: list[dict[str, Any]] = []
    _patch_upsert(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_tokens',
            json={'device_id': FAKE_DEVICE_ID, 'token': FAKE_TOKEN},
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    assert calls[0]['channel'] == 'umeng_push'


# ---------------------------------------------------------------------------
# POST: upsert 语义 (同 device_id 重复)
# ---------------------------------------------------------------------------


def test_post_register_upserts_on_repeat_device(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """同 device_id + channel 第二次 → service 被调用两次, 第二次 token 更新 (upsert 语义)."""
    calls: list[dict[str, Any]] = []
    _patch_upsert(monkeypatch, calls)

    with TestClient(test_app) as client:
        first = client.post(
            '/api/v1/app/push_tokens',
            json={
                'device_id': FAKE_DEVICE_ID,
                'channel': 'umeng_push',
                'token': FAKE_TOKEN,
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )
        second = client.post(
            '/api/v1/app/push_tokens',
            json={
                'device_id': FAKE_DEVICE_ID,
                'channel': 'umeng_push',
                'token': 'umeng_tok_' + 'B' * 40,
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 2
    assert calls[0]['device_id'] == calls[1]['device_id'] == FAKE_DEVICE_ID
    assert calls[0]['token'] != calls[1]['token'], 'upsert 语义: 第二次 token 应覆盖'


# ---------------------------------------------------------------------------
# POST: 未登录 / 非法 channel
# ---------------------------------------------------------------------------


def test_post_register_returns_401_when_unauthenticated(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_tokens',
            json={
                'device_id': FAKE_DEVICE_ID,
                'channel': 'umeng_push',
                'token': FAKE_TOKEN,
            },
        )
    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text


def test_upsert_service_rejects_unknown_channel() -> None:
    """channel 白名单保护 (M1 只允许 umeng_push) — 业务函数层校验.

    HTTP 层的 400 由全局 `BaseExceptionError` handler 完成 (见
    backend/common/exception/exception_handler.py register_exception);
    本测试只需验证 RequestError 被抛出 + code=400 语义, 避免在最小测试 app
    中拼装完整异常处理栈。
    """
    with pytest.raises(errors.RequestError) as exc_info:
        asyncio.run(
            push_tokens_module.upsert_push_token_for_user(
                db=SimpleNamespace(),
                user_id=FAKE_USER_ID,
                device_id=FAKE_DEVICE_ID,
                channel='fcm',
                token=FAKE_TOKEN,
            )
        )
    assert exc_info.value.code == HTTP_400_BAD_REQUEST
    assert 'fcm' in str(exc_info.value.msg)


# ---------------------------------------------------------------------------
# DELETE: 级联 + 鉴权
# ---------------------------------------------------------------------------


def test_delete_returns_200_and_calls_service_with_device_id(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_delete(monkeypatch, calls, rowcount=2)

    with TestClient(test_app) as client:
        resp = client.delete(
            f'/api/v1/app/push_tokens/{FAKE_DEVICE_ID}',
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    assert len(calls) == 1
    assert calls[0]['user_id'] == FAKE_USER_ID
    assert calls[0]['device_id'] == FAKE_DEVICE_ID


def test_delete_is_idempotent_when_no_rows(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """已清空设备再 DELETE → 仍 200 (幂等)."""
    calls: list[dict[str, Any]] = []
    _patch_delete(monkeypatch, calls, rowcount=0)

    with TestClient(test_app) as client:
        resp = client.delete(
            f'/api/v1/app/push_tokens/{FAKE_DEVICE_ID}',
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 200, resp.text


def test_delete_returns_401_when_unauthenticated(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.delete(f'/api/v1/app/push_tokens/{FAKE_DEVICE_ID}')
    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text


# ---------------------------------------------------------------------------
# Schema 契约: RegisterPushTokenRequest
# ---------------------------------------------------------------------------


def test_register_request_requires_device_id_and_token() -> None:
    """空 device_id / 空 token → Pydantic 拒绝."""
    with pytest.raises(ValueError):
        push_tokens_module.RegisterPushTokenRequest(
            device_id='', token=FAKE_TOKEN,
        )
    with pytest.raises(ValueError):
        push_tokens_module.RegisterPushTokenRequest(
            device_id=FAKE_DEVICE_ID, token='',
        )


def test_register_request_channel_defaults_to_umeng_push() -> None:
    """channel 未传 → 默认 umeng_push."""
    req = push_tokens_module.RegisterPushTokenRequest(
        device_id=FAKE_DEVICE_ID, token=FAKE_TOKEN,
    )
    assert req.channel == 'umeng_push'
