"""NewApiAdminClient 连接池迁移真实 HTTP 回归（零 mock，真 loopback socket）。

doc25 M1-4 ④：用 stdlib ThreadingHTTPServer 起一个模拟 new-api 管理面的真 HTTP 服务，
真 httpx 经进程级连接池单例打它，验证 M1-3 的三处 carve-out：

1. **base_url 绝对化**：池单例无 base_url，``_request`` 默认路径须拼 ``self.base_url + path``，
   否则相对路径打不出去（8 消费方全断）。
2. **登录流 cookie jar 隔离**：``bootstrap_user_access_token`` 用独立短命 client 持 login 会话，
   cookie 不污染走连接池的 admin 调用。
3. **绝不关池**：admin 调用走池后不得 aclose 池单例（否则进程内后续调用全挂）。

附带验证 newapi 鉴权是**裸 Authorization + New-Api-User**（无 Bearer 前缀）、``/status`` 免鉴权、
健康探活经池命中 ``/api/status`` 显示 up。
"""

from __future__ import annotations

import json
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from backend.common import service_http, services_config
from backend.common.service_health import check_service_health
from backend.common.service_registry import get_service_spec

if TYPE_CHECKING:
    from collections.abc import Iterator


class _NewApiHandler(BaseHTTPRequestHandler):
    """模拟 new-api 管理面（路径前缀 /api）：记录每次请求的鉴权头/cookie，供隔离性断言。"""

    def _record(self) -> None:
        self.server.requests_log.append(  # type: ignore[attr-defined]
            {
                'method': self.command,
                'path': self.path,
                'auth': self.headers.get('Authorization'),
                'newapi_user': self.headers.get('New-Api-User'),
                'cookie': self.headers.get('Cookie'),
            }
        )

    def _respond(self, code: int, payload: dict, *, set_cookie: str | None = None) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        if set_cookie:
            self.send_header('Set-Cookie', set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self) -> None:
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)

    def do_GET(self) -> None:
        self._record()
        if self.path == '/api/status':  # 免鉴权
            self._respond(200, {'success': True, 'data': {'quota_per_unit': 500000}})
        elif self.path.startswith('/api/user/token'):  # 需 login 后的 session cookie
            if self.headers.get('Cookie'):
                # PostgreSQL char(32) 可能把令牌右侧补空格；客户端边界必须归一化后再写 HTTP 头。
                self._respond(200, {'success': True, 'data': 'user-access-token-xyz   '})
            else:
                self._respond(401, {'success': False, 'message': 'no session cookie'})
        elif self.path.startswith('/api/user/'):  # GET /api/user/{id}
            uid = self.path.rsplit('/', 1)[-1]
            self._respond(
                200,
                {'success': True, 'data': {'id': int(uid), 'quota': 123, 'used_quota': 7, 'request_count': 2}},
            )
        else:
            self._respond(404, {'success': False, 'message': 'not found'})

    def do_POST(self) -> None:
        self._drain_body()
        self._record()
        if self.path == '/api/user/login':  # 下发 session cookie（仅落入发起方 client 的 jar）
            self._respond(200, {'success': True, 'data': None}, set_cookie='session=sess-abc; Path=/')
        else:
            self._respond(404, {'success': False, 'message': 'not found'})

    def do_PUT(self) -> None:
        self._drain_body()
        self._record()
        if self.path == '/api/user/':  # set_user_password / UpdateUser
            self._respond(200, {'success': True, 'data': None})
        else:
            self._respond(404, {'success': False, 'message': 'not found'})

    def log_message(self, *args: object) -> None:  # 静音
        pass


@pytest.fixture
def newapi_server() -> Iterator[ThreadingHTTPServer]:
    """真实 loopback new-api 模拟服务；``server.requests_log`` 记录全部请求。"""
    server = ThreadingHTTPServer(('127.0.0.1', 0), _NewApiHandler)
    server.requests_log = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离真实 services.toml + 清进程级连接池单例（避免跨测试复用旧 loop 的 client）。"""
    monkeypatch.setenv('HUANXING_SERVICES_CONFIG', '/nonexistent/services.toml')
    monkeypatch.delenv('HUANXING_INTERNAL_SERVICE_SECRET', raising=False)
    services_config.reload_services_config()
    service_http._clients.clear()
    yield
    service_http._clients.clear()
    services_config.reload_services_config()


def _make_client(monkeypatch: pytest.MonkeyPatch, server: ThreadingHTTPServer):
    """构造一个指向 loopback 服务的 NewApiAdminClient（新实例，非 import 期冻结的单例）。"""
    from backend.app.newapi.client import NewApiAdminClient

    port = server.server_address[1]
    monkeypatch.setenv('NEWAPI_ADMIN_BASE_URL', f'http://127.0.0.1:{port}/api')
    monkeypatch.setenv('NEWAPI_ADMIN_ACCESS_TOKEN', 'admin-tok')
    return NewApiAdminClient(admin_user_id=1)


@pytest.mark.asyncio
async def test_pooled_admin_call_absolute_url_and_envelope(
    monkeypatch: pytest.MonkeyPatch, newapi_server: ThreadingHTTPServer
) -> None:
    """admin 调用走连接池：相对 path 被拼成绝对 URL（命中 /api/user/1），信封解包出 data。"""
    client = _make_client(monkeypatch, newapi_server)

    user = await client.get_user(1)

    assert user == {'id': 1, 'quota': 123, 'used_quota': 7, 'request_count': 2}
    log = newapi_server.requests_log  # type: ignore[attr-defined]
    assert [r['path'] for r in log] == ['/api/user/1']  # 绝对化命中（含 /api 前缀）
    # newapi 鉴权是裸 Authorization（无 Bearer 前缀）+ New-Api-User
    assert log[0]['auth'] == 'admin-tok'
    assert log[0]['newapi_user'] == '1'
    # 关键回归：池单例调用后**绝不**被关闭
    assert service_http.get_service_client('newapi').is_closed is False


@pytest.mark.asyncio
async def test_status_unauthenticated_via_pool(
    monkeypatch: pytest.MonkeyPatch, newapi_server: ThreadingHTTPServer
) -> None:
    """/status 免鉴权，经池命中 /api/status，解出 quota_per_unit。"""
    client = _make_client(monkeypatch, newapi_server)

    quota = await client.get_quota_per_unit()

    assert quota == 500000
    log = newapi_server.requests_log  # type: ignore[attr-defined]
    assert log[0]['path'] == '/api/status'


@pytest.mark.asyncio
async def test_pool_singleton_reused_across_calls(
    monkeypatch: pytest.MonkeyPatch, newapi_server: ThreadingHTTPServer
) -> None:
    """多次 admin 调用复用同一进程级连接池单例（连接复用），且单例始终存活。"""
    client = _make_client(monkeypatch, newapi_server)

    first = service_http.get_service_client('newapi')
    await client.get_user(1)
    await client.get_user(2)
    second = service_http.get_service_client('newapi')

    assert first is second  # 同一单例
    assert second.is_closed is False
    assert len(newapi_server.requests_log) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_login_flow_isolated_cookie_jar(
    monkeypatch: pytest.MonkeyPatch, newapi_server: ThreadingHTTPServer
) -> None:
    """登录流用独立 client 持 cookie jar：login session cookie 不污染走池的 admin 调用。"""
    client = _make_client(monkeypatch, newapi_server)

    token = await client.bootstrap_user_access_token(newapi_user_id=5, username='u5')
    # 之后再发一次走池的 admin 调用，验证池 client 未被 login cookie 污染
    await client.get_status()

    assert token == 'user-access-token-xyz'

    log = newapi_server.requests_log  # type: ignore[attr-defined]
    by_path = {(r['method'], r['path']): r for r in log}

    # set_user_password 走池（admin 鉴权，无 cookie）
    put = by_path['PUT', '/api/user/']
    assert put['auth'] == 'admin-tok'
    assert put['cookie'] is None

    # login 用独立 client（首次无 cookie），随后 GET /user/token 在同一独立 client 上**带** cookie
    login = by_path['POST', '/api/user/login']
    assert login['cookie'] is None
    token_req = next(r for r in log if r['method'] == 'GET' and r['path'].startswith('/api/user/token'))
    assert token_req['cookie'] is not None and 'sess-abc' in token_req['cookie']

    # 关键隔离断言：bootstrap 之后走池的 admin 调用（/status）**不带** login session cookie
    status_req = next(r for r in log if r['path'] == '/api/status')
    assert status_req['cookie'] is None


@pytest.mark.asyncio
async def test_health_check_status_up_via_pool(
    monkeypatch: pytest.MonkeyPatch, newapi_server: ThreadingHTTPServer
) -> None:
    """newapi 健康探活：pooled=True 经池 GET /api/status → 200 → status=up（不要求 version）。"""
    port = newapi_server.server_address[1]
    monkeypatch.setenv('NEWAPI_ADMIN_BASE_URL', f'http://127.0.0.1:{port}/api')

    report = await check_service_health(get_service_spec('newapi'))

    assert report.status == 'up'
    assert report.latency_ms is not None
    log = newapi_server.requests_log  # type: ignore[attr-defined]
    assert any(r['path'] == '/api/status' for r in log)
