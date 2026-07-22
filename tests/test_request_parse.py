"""请求来源解析回归。"""

from __future__ import annotations

from starlette.requests import Request

from backend.utils.request_parse import get_request_ip


def _request(*, headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None) -> Request:
    scope: dict[str, object] = {
        'type': 'http',
        'method': 'GET',
        'path': '/',
        'headers': headers,
    }
    if client is not None:
        scope['client'] = client
    return Request(scope)


def test_get_request_ip_prefers_proxy_headers() -> None:
    request = _request(
        headers=[(b'x-real-ip', b'203.0.113.7'), (b'x-forwarded-for', b'198.51.100.1, 198.51.100.2')],
        client=('192.0.2.5', 8000),
    )

    assert get_request_ip(request) == '203.0.113.7'


def test_get_request_ip_uses_first_forwarded_address() -> None:
    request = _request(
        headers=[(b'x-forwarded-for', b'198.51.100.1, 198.51.100.2')],
        client=('192.0.2.5', 8000),
    )

    assert get_request_ip(request) == '198.51.100.1'


def test_get_request_ip_handles_missing_client() -> None:
    request = _request(headers=[], client=None)

    assert get_request_ip(request) == '0.0.0.0'
