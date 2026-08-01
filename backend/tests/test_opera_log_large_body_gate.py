"""操作日志中间件的大请求体闸（IMG4-H-01）。

背景：`get_request_args` 曾在路由之前无条件 `await request.body()`，把整个 multipart
请求体缓冲进内存，非 JSON 分支还会再 decode 出一份等长字符串。发布数百 MB 的引擎包 /
模型包时，端点侧的分块读取与提前释放事务因此完全失效——817 MB 的模型包实测让生产
nginx 在 600 秒 `proxy_read_timeout` 上返回 504。

本测试钉死：大文件与超阈值请求体只记录形状，绝不触碰 `request.body()`。
"""

from __future__ import annotations

import pytest

from starlette.requests import Request

from backend.core.conf import settings
from backend.middleware.opera_log_middleware import OperaLogMiddleware


def _request(headers: list[tuple[bytes, bytes]], path: str = '/api/v1/hasn/app-catalogs/804/model-package-stage') -> Request:
    return Request(
        {
            'type': 'http',
            'http_version': '1.1',
            'method': 'POST',
            'path': path,
            'raw_path': path.encode(),
            'query_string': b'',
            'root_path': '',
            'headers': headers,
            'client': ('127.0.0.1', 12345),
            'scheme': 'http',
            'server': ('127.0.0.1', 8020),
            'path_params': {},
        }
    )


def _middleware() -> OperaLogMiddleware:
    return OperaLogMiddleware(app=lambda scope, receive, send: None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_multipart_upload_body_is_never_buffered(monkeypatch: pytest.MonkeyPatch) -> None:
    """multipart 上传只记录形状；`request.body()` 一次都不能被调用。"""
    calls = 0

    async def _explode(self: Request) -> bytes:  # noqa: ANN401
        nonlocal calls
        calls += 1
        raise AssertionError('大文件上传不得读取请求体')

    monkeypatch.setattr(Request, 'body', _explode, raising=True)

    request = _request(
        [
            (b'content-type', b'multipart/form-data; boundary=----abc'),
            (b'content-length', str(817 * 1024 * 1024).encode()),
        ]
    )
    args = await _middleware().get_request_args(request)

    assert calls == 0
    assert args['data'].startswith('[BODY OMITTED: multipart/form-data')
    assert str(817 * 1024 * 1024) in args['data']


@pytest.mark.asyncio
async def test_oversized_non_multipart_body_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 multipart 但超阈值的请求体同样短路，覆盖裸 octet-stream 直传。"""

    async def _explode(self: Request) -> bytes:  # noqa: ANN401
        raise AssertionError('超阈值请求体不得读取')

    monkeypatch.setattr(Request, 'body', _explode, raising=True)

    request = _request(
        [
            (b'content-type', b'application/octet-stream'),
            (b'content-length', str(settings.OPERA_LOG_MAX_BODY_BYTES + 1).encode()),
        ]
    )
    args = await _middleware().get_request_args(request)

    assert args['data'].startswith('[BODY OMITTED: application/octet-stream')


@pytest.mark.asyncio
async def test_small_json_body_still_recorded() -> None:
    """阈值内的普通 JSON 请求照常记录，闸门不得误伤常规审计。"""
    payload = b'{"name":"\\u56fe\\u574a"}'
    request = _request(
        [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(payload)).encode()),
        ]
    )

    async def _receive() -> dict:
        return {'type': 'http.request', 'body': payload, 'more_body': False}

    request._receive = _receive  # type: ignore[attr-defined]
    args = await _middleware().get_request_args(request)

    assert 'BODY OMITTED' not in str(args.get('data', ''))
    assert args.get('json') == {'name': '图坊'}


@pytest.mark.asyncio
async def test_missing_content_length_falls_back_to_content_type_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chunked 上传没有 Content-Length；仍须靠 multipart 判定短路，不得回落到全量缓冲。"""

    async def _explode(self: Request) -> bytes:  # noqa: ANN401
        raise AssertionError('chunked multipart 上传不得读取请求体')

    monkeypatch.setattr(Request, 'body', _explode, raising=True)

    request = _request([(b'content-type', b'multipart/form-data; boundary=----abc')])
    args = await _middleware().get_request_args(request)

    assert args['data'].startswith('[BODY OMITTED: multipart/form-data, 0 bytes]')
