"""B7 - POST /api/v1/app/push_receipts 端点契约测试.

测试策略 (与 B4 test_push_tokens.py 一致):
- 最小 FastAPI 测试 app, 只挂被测 router, 通过 `app.dependency_overrides`
  替换 JWT 依赖 + CurrentSessionTransaction。
- monkeypatch 业务函数 `record_push_receipt_for_user` 以解耦 DB (避免
  aiosqlite 依赖)。
- 单独一条 "DB round-trip" 契约测试: 直接调用 `record_push_receipt_for_user`,
  注入只记录 `add/flush` 调用的 fake_db, 验证 "POST → DB 多 1 行" 语义 (B7
  acceptance 条目) — 这是不经路由层的业务层契约。

覆盖 B7 acceptance:
1. POST 路由挂在 router 上
2. POST 正常路径 → 200, 业务函数收到 trace_id / received_at_unix_ms / channel
3. 业务函数把一行 PushReceipt add 到 db + flush (DB 多 1 行)
4. POST 未登录 → 401
5. POST channel 非法 → 业务函数抛 RequestError
6. POST trace_id 为空 / received_at_unix_ms <=0 → Pydantic 422
7. received_at_unix_ms 换算 UTC datetime 正确
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

from backend.app.hasn_client.api import push_receipts as push_receipts_module
from backend.app.hasn_client.model.push_receipt import PushReceipt
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

FAKE_USER_ID = 42
FAKE_HASN_ID = 'h_100001'
FAKE_TRACE_ID = 'conv:c_test_0001'
FAKE_RECEIVED_AT_MS = 1_745_241_900_000  # 2026-04-21T13:45:00+00:00


def _fake_db_transaction() -> Generator[SimpleNamespace, None, None]:
    yield SimpleNamespace()


def _fake_jwt_auth_ok(request: Request) -> str:
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    fake_user = SimpleNamespace(id=FAKE_USER_ID, username='tester')
    request.scope['user'] = fake_user
    request.scope['auth'] = ['authenticated']
    return auth[7:]


@pytest.fixture
def test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(push_receipts_module.router, prefix='/api/v1/app/push_receipts')
    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_ok
    app.dependency_overrides[get_db_transaction] = _fake_db_transaction
    return app


def _patch_record(
    monkeypatch: pytest.MonkeyPatch, recorder: list[dict[str, Any]]
) -> None:
    async def fake_record(  # noqa: RUF029 — must be async to match awaited signature
        db: object,
        *,
        user_id: int,
        trace_id: str,
        received_at_unix_ms: int,
        channel: str,
    ) -> PushReceipt:
        recorder.append(
            {
                'user_id': user_id,
                'trace_id': trace_id,
                'received_at_unix_ms': received_at_unix_ms,
                'channel': channel,
            }
        )
        received_at = datetime.fromtimestamp(
            received_at_unix_ms / 1000.0, tz=timezone.utc
        )
        return PushReceipt(
            trace_id=trace_id,
            hasn_id=FAKE_HASN_ID,
            channel=channel,
            received_at=received_at,
        )

    monkeypatch.setattr(
        push_receipts_module, 'record_push_receipt_for_user', fake_record
    )


# ---------------------------------------------------------------------------
# 路由挂载契约
# ---------------------------------------------------------------------------


def test_router_exposes_post_root(test_app: FastAPI) -> None:
    paths_methods = {
        (getattr(route, 'path'), tuple(sorted(getattr(route, 'methods'))))
        for route in test_app.routes
        if hasattr(route, 'methods') and getattr(route, 'methods', None)
    }
    assert ('/api/v1/app/push_receipts', ('POST',)) in paths_methods, (
        f'POST /api/v1/app/push_receipts 路由缺失; 实际={paths_methods}'
    )


# ---------------------------------------------------------------------------
# POST: 正常路径
# ---------------------------------------------------------------------------


def test_post_returns_200_and_calls_service(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_record(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_receipts',
            json={
                'trace_id': FAKE_TRACE_ID,
                'received_at_unix_ms': FAKE_RECEIVED_AT_MS,
                'channel': 'umeng_push',
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    data = body['data']
    assert data['trace_id'] == FAKE_TRACE_ID
    assert data['channel'] == 'umeng_push'
    assert 'received_at' in data

    assert len(calls) == 1
    assert calls[0]['user_id'] == FAKE_USER_ID
    assert calls[0]['trace_id'] == FAKE_TRACE_ID
    assert calls[0]['received_at_unix_ms'] == FAKE_RECEIVED_AT_MS
    assert calls[0]['channel'] == 'umeng_push'


def test_post_defaults_channel_to_umeng_push(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不传 channel 字段 → 默认 'umeng_push' (M1 唯一 channel)."""
    calls: list[dict[str, Any]] = []
    _patch_record(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_receipts',
            json={
                'trace_id': FAKE_TRACE_ID,
                'received_at_unix_ms': FAKE_RECEIVED_AT_MS,
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    assert calls[0]['channel'] == 'umeng_push'


# ---------------------------------------------------------------------------
# POST: 鉴权 / 非法入参
# ---------------------------------------------------------------------------


def test_post_returns_401_when_unauthenticated(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_receipts',
            json={
                'trace_id': FAKE_TRACE_ID,
                'received_at_unix_ms': FAKE_RECEIVED_AT_MS,
                'channel': 'umeng_push',
            },
        )
    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text


def test_post_rejects_empty_trace_id(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_receipts',
            json={
                'trace_id': '',
                'received_at_unix_ms': FAKE_RECEIVED_AT_MS,
                'channel': 'umeng_push',
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


def test_post_rejects_nonpositive_received_at_ms(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/push_receipts',
            json={
                'trace_id': FAKE_TRACE_ID,
                'received_at_unix_ms': 0,
                'channel': 'umeng_push',
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 业务层契约: channel 白名单
# ---------------------------------------------------------------------------


def test_record_service_rejects_unknown_channel() -> None:
    """channel 白名单 (M1 只允许 umeng_push) — 业务函数层校验."""
    with pytest.raises(errors.RequestError) as exc_info:
        asyncio.run(
            push_receipts_module.record_push_receipt_for_user(
                db=SimpleNamespace(),
                user_id=FAKE_USER_ID,
                trace_id=FAKE_TRACE_ID,
                received_at_unix_ms=FAKE_RECEIVED_AT_MS,
                channel='fcm',
            )
        )
    assert exc_info.value.code == HTTP_400_BAD_REQUEST
    assert 'fcm' in str(exc_info.value.msg)


# ---------------------------------------------------------------------------
# 业务层契约: "DB 多 1 行" (POST → 落库) + received_at 换算正确
# ---------------------------------------------------------------------------


class _FakeDb:
    """只记录 add/flush 调用的最小 db 替身."""

    def __init__(self, hasn_id: str) -> None:
        self._hasn_id = hasn_id
        self.added_rows: list[PushReceipt] = []
        self.flush_count = 0

    async def execute(self, _stmt: object) -> SimpleNamespace:
        hasn_id = self._hasn_id

        class _Scalars:
            def first(self) -> SimpleNamespace:
                return SimpleNamespace(hasn_id=hasn_id)

        return SimpleNamespace(scalars=lambda: _Scalars())

    def add(self, row: object) -> None:
        assert isinstance(row, PushReceipt)
        self.added_rows.append(row)

    async def flush(self) -> None:
        self.flush_count += 1


def test_record_service_adds_one_row_with_correct_fields() -> None:
    """B7 acceptance: POST → DB 多 1 行 (业务函数级验证).

    不依赖 aiosqlite; 用 _FakeDb 记录 add/flush 调用 + 验证 ORM 对象字段。
    """
    db = _FakeDb(hasn_id=FAKE_HASN_ID)

    row = asyncio.run(
        push_receipts_module.record_push_receipt_for_user(
            db=db,
            user_id=FAKE_USER_ID,
            trace_id=FAKE_TRACE_ID,
            received_at_unix_ms=FAKE_RECEIVED_AT_MS,
            channel='umeng_push',
        )
    )

    assert len(db.added_rows) == 1, '应 add 一行 push_receipts'
    assert db.flush_count == 1
    assert db.added_rows[0] is row
    assert row.trace_id == FAKE_TRACE_ID
    assert row.hasn_id == FAKE_HASN_ID
    assert row.channel == 'umeng_push'
    expected = datetime.fromtimestamp(
        FAKE_RECEIVED_AT_MS / 1000.0, tz=timezone.utc
    )
    assert row.received_at == expected


# ---------------------------------------------------------------------------
# Schema 契约
# ---------------------------------------------------------------------------


def test_report_request_requires_nonempty_trace_id() -> None:
    with pytest.raises(ValueError):
        push_receipts_module.ReportPushReceiptRequest(
            trace_id='',
            received_at_unix_ms=FAKE_RECEIVED_AT_MS,
            channel='umeng_push',
        )


def test_report_request_requires_positive_received_at_ms() -> None:
    with pytest.raises(ValueError):
        push_receipts_module.ReportPushReceiptRequest(
            trace_id=FAKE_TRACE_ID,
            received_at_unix_ms=0,
            channel='umeng_push',
        )
