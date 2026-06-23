"""service_health 测试：真实 loopback HTTP 服务 + 真实闭合端口探测（零 mock）。

用 stdlib ThreadingHTTPServer 起一个真 HTTP 服务做探活目标（健康端点/任意路径），
用一个确定关闭的高端口做不可达探测——全程真实 socket，不打桩、不造假。
"""

from __future__ import annotations

import socket
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from backend.common.service_health import (
    ServiceHealthReport,
    _extract_version,
    check_all_services_health,
    check_service_health,
)
from backend.common.service_registry import get_service_spec, iter_services
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Iterator


class _HealthHandler(BaseHTTPRequestHandler):
    """/v1/healthz → 200 {ok,version}；其它路径 → 404（仍是一个 HTTP 响应=可达）。"""

    def do_GET(self) -> None:
        if self.path == '/v1/healthz':
            body = b'{"ok": true, "version": "9.9.9"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

    def log_message(self, *args: object) -> None:  # 静音
        pass


@pytest.fixture
def health_server() -> Iterator[int]:
    """真实 loopback HTTP 服务，返回其端口；测试结束关闭。"""
    server = ThreadingHTTPServer(('127.0.0.1', 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    """绑定再释放一个端口，得到一个确定无人监听的端口号（真实 connection refused）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_extract_version() -> None:
    assert _extract_version({'version': '1.2.3'}) == '1.2.3'
    assert _extract_version({'app_version': 7}) == '7'
    assert _extract_version({'nope': 'x'}) is None
    assert _extract_version('not a dict') is None


@pytest.mark.asyncio
async def test_unconfigured_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod + 未配 → status=unconfigured，不发网络（latency 为 None）。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod', raising=False)
    monkeypatch.delenv('QUANT_ENGINE_URL', raising=False)
    monkeypatch.setattr(settings, 'QUANT_ENGINE_URL', '', raising=False)

    report = await check_service_health(get_service_spec('quant'))

    assert report.status == 'unconfigured'
    assert report.configured is False
    assert report.latency_ms is None
    assert not report.base_url


@pytest.mark.asyncio
async def test_up_via_health_path(monkeypatch: pytest.MonkeyPatch, health_server: int) -> None:
    """有 health_path 的服务（quant）：真实 200 健康响应 → status=up + 读到 version。"""
    monkeypatch.setenv('QUANT_ENGINE_URL', f'http://127.0.0.1:{health_server}')
    monkeypatch.delenv('QUANT_ENGINE_TOKEN', raising=False)

    report = await check_service_health(get_service_spec('quant'))

    assert report.status == 'up'
    assert report.version == '9.9.9'
    assert report.latency_ms is not None
    assert report.detail == 'ok'


@pytest.mark.asyncio
async def test_reachable_non_pooled_any_http(
    monkeypatch: pytest.MonkeyPatch, health_server: int
) -> None:
    """无 health_path 的服务（ragflow）：根路径返回 404 仍算可达 up（自有鉴权不在探测职责内）。"""
    monkeypatch.setenv('RAGFLOW_PUBLIC_URL', f'http://127.0.0.1:{health_server}')

    report = await check_service_health(get_service_spec('ragflow'))

    assert report.status == 'up'
    assert 'HTTP 404' in report.detail


@pytest.mark.asyncio
async def test_down_when_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了地址但无人监听 → 真实 connection refused → status=down。"""
    monkeypatch.setenv('QUANT_ENGINE_URL', f'http://127.0.0.1:{_closed_port()}')

    report = await check_service_health(get_service_spec('quant'))

    assert report.status == 'down'
    assert report.configured is True
    assert report.latency_ms is not None


@pytest.mark.asyncio
async def test_check_all_returns_one_per_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod + 全部未配 → 每个登记服务一条报告，全 unconfigured，零网络。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod', raising=False)
    for spec in iter_services():
        monkeypatch.delenv(spec.url_attr, raising=False)
        monkeypatch.setattr(settings, spec.url_attr, '', raising=False)

    reports = await check_all_services_health()

    assert len(reports) == len(iter_services())
    assert all(isinstance(r, ServiceHealthReport) for r in reports)
    assert all(r.status == 'unconfigured' for r in reports)
    names = {r.name for r in reports}
    assert {'finance', 'quant', 'ragflow', 'hermes', 'newapi'} <= names
