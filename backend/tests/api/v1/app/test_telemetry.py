"""B8 - POST /api/v1/app/telemetry/events 批量埋点端点契约测试.

测试策略 (与 B4 test_push_tokens.py / B7 test_push_receipts.py 一致):
- 最小 FastAPI 测试 app, 只挂被测 router, 通过 `app.dependency_overrides`
  替换 JWT 依赖 + CurrentSessionTransaction。
- monkeypatch 业务函数 `record_telemetry_events_for_user` 以解耦 DB (避免
  aiosqlite 依赖)。
- 单独一条 "DB round-trip" 契约测试: 直接调用 `record_telemetry_events_for_user`,
  注入只记录 `add/flush` 调用的 fake_db, 验证 "POST → DB 多 N 行" 语义。

覆盖 B8 acceptance:
1. POST /events 路由挂在 router 上
2. POST 正常路径 → 200 ingested=N, 业务函数收到 events
3. 10+ 个 §6.1 event_type 全部被 schema 接受 (白名单全覆盖)
4. POST 未登录 → 401
5. POST 空 events / 非法 event_type / 非正数 occurred_at_unix_ms → 422 或 400
6. 批量超上限 (>100) → 422
7. 业务层: event_type 非白名单 → RequestError 400
8. 业务层: 批量 N 条 → DB 多 N 行, properties 保持不变, occurred_at 换算正确
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

from backend.app.hasn_client.api import telemetry as telemetry_module
from backend.app.hasn_client.model.telemetry_event import (
    TELEMETRY_EVENT_TYPE_VALUES,
    TelemetryEvent,
    TelemetryEventType,
)
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

FAKE_USER_ID = 42
FAKE_HASN_ID = 'h_100001'
FAKE_OCCURRED_AT_MS = 1_745_241_900_000  # 2026-04-21T13:45:00+00:00 (synthetic)


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
    app.include_router(telemetry_module.router, prefix='/api/v1/app/telemetry')
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
        events: list[Any],
    ) -> list[TelemetryEvent]:
        rows: list[TelemetryEvent] = []
        for evt in events:
            recorder.append(
                {
                    'user_id': user_id,
                    'event_type': evt.event_type,
                    'occurred_at_unix_ms': evt.occurred_at_unix_ms,
                    'properties': evt.properties,
                }
            )
            occurred_at = datetime.fromtimestamp(
                evt.occurred_at_unix_ms / 1000.0, tz=timezone.utc
            )
            rows.append(
                TelemetryEvent(
                    hasn_id=FAKE_HASN_ID,
                    event_type=evt.event_type,
                    properties=evt.properties,
                    occurred_at=occurred_at,
                )
            )
        return rows

    monkeypatch.setattr(
        telemetry_module, 'record_telemetry_events_for_user', fake_record
    )


# ---------------------------------------------------------------------------
# 路由挂载契约
# ---------------------------------------------------------------------------


def test_router_exposes_post_events(test_app: FastAPI) -> None:
    paths_methods = {
        (getattr(route, 'path'), tuple(sorted(getattr(route, 'methods'))))
        for route in test_app.routes
        if hasattr(route, 'methods') and getattr(route, 'methods', None)
    }
    assert ('/api/v1/app/telemetry/events', ('POST',)) in paths_methods, (
        f'POST /api/v1/app/telemetry/events 路由缺失; 实际={paths_methods}'
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
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': 'auth.login_success',
                        'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
                        'properties': {'duration_ms': 123, 'method': 'password'},
                    }
                ],
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    assert body['data'] == {'ingested': 1}

    assert len(calls) == 1
    assert calls[0]['user_id'] == FAKE_USER_ID
    assert calls[0]['event_type'] == 'auth.login_success'
    assert calls[0]['occurred_at_unix_ms'] == FAKE_OCCURRED_AT_MS
    assert calls[0]['properties'] == {'duration_ms': 123, 'method': 'password'}


def test_post_accepts_null_properties(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """properties 可选: 不传 / null 均接受."""
    calls: list[dict[str, Any]] = []
    _patch_record(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': 'auth.login_start',
                        'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
                    }
                ],
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    assert calls[0]['properties'] is None


def test_post_accepts_batch_of_multiple_events(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批量上报 3 条 → 业务函数收到 3 条, ingested=3."""
    calls: list[dict[str, Any]] = []
    _patch_record(monkeypatch, calls)

    payload_events = [
        {
            'event_type': 'im.conversation_opened',
            'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
            'properties': {'conversation_id_hash': 'hash_a', 'unread': 2},
        },
        {
            'event_type': 'im.message_sent',
            'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS + 100,
            'properties': {'conversation_id_hash': 'hash_a', 'content_type': 'text'},
        },
        {
            'event_type': 'push.wakeup',
            'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS + 200,
            'properties': {'channel': 'umeng_push', 'trace_id': 'conv:c_x'},
        },
    ]

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={'events': payload_events},
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()['data'] == {'ingested': 3}
    assert len(calls) == 3
    assert [c['event_type'] for c in calls] == [
        'im.conversation_opened',
        'im.message_sent',
        'push.wakeup',
    ]


# ---------------------------------------------------------------------------
# Schema: §6.1 全部 event_type 白名单覆盖 (acceptance "10 类 event_type")
# ---------------------------------------------------------------------------


_ALL_EVENT_TYPES: list[str] = sorted(TELEMETRY_EVENT_TYPE_VALUES)


def test_whitelist_contains_at_least_ten_types() -> None:
    """§6.1 枚举至少 10 类 (含 runtime.*; 本 milestone 文档给出 11 条)."""
    assert len(_ALL_EVENT_TYPES) >= 10, (
        f'§6.1 event_type 白名单不足 10 类; 实际={_ALL_EVENT_TYPES}'
    )


@pytest.mark.parametrize('event_type', _ALL_EVENT_TYPES)
def test_post_accepts_every_whitelisted_event_type(
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    """每个 §6.1 白名单 event_type 都能通过 schema + 业务函数接受."""
    calls: list[dict[str, Any]] = []
    _patch_record(monkeypatch, calls)

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': event_type,
                        'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
                    }
                ],
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )

    assert resp.status_code == 200, (
        f'event_type={event_type!r} 未被接受: {resp.status_code} {resp.text}'
    )
    assert calls[0]['event_type'] == event_type


# ---------------------------------------------------------------------------
# POST: 鉴权 / 非法入参 → 422 / 401
# ---------------------------------------------------------------------------


def test_post_returns_401_when_unauthenticated(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': 'auth.login_start',
                        'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
                    }
                ],
            },
        )
    assert resp.status_code == HTTP_401_UNAUTHORIZED, resp.text


def test_post_rejects_empty_events_list(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={'events': []},
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


def test_post_rejects_batch_over_limit(test_app: FastAPI) -> None:
    oversized = [
        {
            'event_type': 'auth.login_start',
            'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS + i,
        }
        for i in range(telemetry_module.MAX_BATCH_SIZE + 1)
    ]
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={'events': oversized},
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


def test_post_rejects_nonpositive_occurred_at_ms(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': 'auth.login_start',
                        'occurred_at_unix_ms': 0,
                    }
                ],
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


def test_post_rejects_empty_event_type(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/app/telemetry/events',
            json={
                'events': [
                    {
                        'event_type': '',
                        'occurred_at_unix_ms': FAKE_OCCURRED_AT_MS,
                    }
                ],
            },
            headers={'Authorization': 'Bearer fake-jwt'},
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 业务层契约: event_type 非白名单 → RequestError 400
# ---------------------------------------------------------------------------


class _FakeDb:
    """只记录 add/flush 调用的最小 db 替身."""

    def __init__(self, hasn_id: str) -> None:
        self._hasn_id = hasn_id
        self.added_rows: list[TelemetryEvent] = []
        self.flush_count = 0

    async def execute(self, _stmt: object) -> SimpleNamespace:
        hasn_id = self._hasn_id

        class _Scalars:
            def first(self) -> SimpleNamespace:
                return SimpleNamespace(hasn_id=hasn_id)

        return SimpleNamespace(scalars=lambda: _Scalars())

    def add(self, row: object) -> None:
        assert isinstance(row, TelemetryEvent)
        self.added_rows.append(row)

    async def flush(self) -> None:
        self.flush_count += 1


def _build_payload(event_type: str, ms: int = FAKE_OCCURRED_AT_MS, props: Any = None):
    """Helper: 构造 TelemetryEventPayload."""
    return telemetry_module.TelemetryEventPayload(
        event_type=event_type,
        occurred_at_unix_ms=ms,
        properties=props,
    )


def test_record_service_rejects_unknown_event_type() -> None:
    """event_type 白名单 (§6.1) — 业务函数层校验."""
    db = _FakeDb(hasn_id=FAKE_HASN_ID)
    payload = _build_payload(event_type='runtime.bogus_kind')

    with pytest.raises(errors.RequestError) as exc_info:
        asyncio.run(
            telemetry_module.record_telemetry_events_for_user(
                db=db,
                user_id=FAKE_USER_ID,
                events=[payload],
            )
        )
    assert exc_info.value.code == HTTP_400_BAD_REQUEST
    assert 'runtime.bogus_kind' in str(exc_info.value.msg)
    # 拒绝后不应落任何行
    assert db.added_rows == []
    assert db.flush_count == 0


# ---------------------------------------------------------------------------
# 业务层契约: "DB 多 N 行" + occurred_at 换算正确 + properties 保持原样
# ---------------------------------------------------------------------------


def test_record_service_adds_n_rows_with_correct_fields() -> None:
    """B8 acceptance: POST → DB 多 N 行 (业务函数级验证).

    不依赖 aiosqlite; 用 _FakeDb 记录 add/flush 调用 + 验证 ORM 对象字段。
    """
    db = _FakeDb(hasn_id=FAKE_HASN_ID)
    payloads = [
        _build_payload(
            event_type=TelemetryEventType.AUTH_LOGIN_SUCCESS.value,
            ms=FAKE_OCCURRED_AT_MS,
            props={'duration_ms': 123},
        ),
        _build_payload(
            event_type=TelemetryEventType.IM_MESSAGE_SENT.value,
            ms=FAKE_OCCURRED_AT_MS + 500,
            props={'conversation_id_hash': 'hash_x', 'content_type': 'text'},
        ),
        _build_payload(
            event_type=TelemetryEventType.RUNTIME_PANICKED.value,
            ms=FAKE_OCCURRED_AT_MS + 1000,
            props={'panic_kind': 'StartNode', 'phase': 'bootstrap'},
        ),
    ]

    rows = asyncio.run(
        telemetry_module.record_telemetry_events_for_user(
            db=db,
            user_id=FAKE_USER_ID,
            events=payloads,
        )
    )

    # acceptance: "DB 多 N 行"
    assert len(db.added_rows) == len(payloads) == 3
    assert db.flush_count == 1, 'flush 应当且仅 1 次 (批处理)'
    assert rows == db.added_rows, '返回顺序应与 add 顺序一致'

    # acceptance: 字段换算正确 + 保序 + hasn_id 来自 JWT (反查 HasnHumans)
    for row, payload in zip(db.added_rows, payloads, strict=True):
        assert row.hasn_id == FAKE_HASN_ID
        assert row.event_type == payload.event_type
        assert row.properties == payload.properties
        expected = datetime.fromtimestamp(
            payload.occurred_at_unix_ms / 1000.0, tz=timezone.utc
        )
        assert row.occurred_at == expected


# ---------------------------------------------------------------------------
# Schema 契约
# ---------------------------------------------------------------------------


def test_request_requires_nonempty_event_type() -> None:
    with pytest.raises(ValueError):
        telemetry_module.TelemetryEventPayload(
            event_type='',
            occurred_at_unix_ms=FAKE_OCCURRED_AT_MS,
        )


def test_request_requires_positive_occurred_at_ms() -> None:
    with pytest.raises(ValueError):
        telemetry_module.TelemetryEventPayload(
            event_type='auth.login_start',
            occurred_at_unix_ms=0,
        )


def test_request_enforces_max_batch_size() -> None:
    oversized = [
        _build_payload(event_type='auth.login_start', ms=FAKE_OCCURRED_AT_MS + i)
        for i in range(telemetry_module.MAX_BATCH_SIZE + 1)
    ]
    with pytest.raises(ValueError):
        telemetry_module.ReportTelemetryEventsRequest(events=oversized)


def test_request_rejects_empty_events() -> None:
    with pytest.raises(ValueError):
        telemetry_module.ReportTelemetryEventsRequest(events=[])
