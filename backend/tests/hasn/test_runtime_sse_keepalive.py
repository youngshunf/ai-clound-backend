"""SSE 心跳保活单测（_with_keepalive，纯 async util，无 PG/HTTP）。

覆盖 daemon↔云端 relay 的 SSE 首帧前静默期保活——避免 nginx proxy_read_timeout 在
沙箱冷启动 + provision + LLM 首 token 的静默窗口切断长连接（症状：daemon 侧
`cloud runtime relay SSE read failed: error decoding response body`）：
  1) 透传：上游正常 yield 字节 → 原样输出，不掺心跳帧；
  2) 心跳：上游静默超过 interval → 插入 SSE 注释帧 `: keepalive\n\n`，上游恢复后续传；
  3) 异常透传：上游抛异常 → 消费侧重抛（不静默吞，保零 fake）。
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.hasn.service import hasn_agent_runtime_dispatch_service as dispatch_module
from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    _SSE_KEEPALIVE_FRAME,
    HasnAgentRuntimeDispatchService,
    _with_keepalive,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


async def _collect(gen: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in gen]


async def test_passes_through_upstream_bytes_without_heartbeat() -> None:
    async def _fast() -> AsyncIterator[bytes]:
        await asyncio.sleep(0)
        yield b'event: message.delta\ndata: {"delta":"hi"}\n\n'
        yield b'event: run.completed\ndata: {}\n\n'

    out = await _collect(_with_keepalive(_fast(), interval=10.0))

    assert out == [
        b'event: message.delta\ndata: {"delta":"hi"}\n\n',
        b'event: run.completed\ndata: {}\n\n',
    ]
    assert _SSE_KEEPALIVE_FRAME not in out


async def test_emits_heartbeat_during_upstream_silence() -> None:
    async def _slow() -> AsyncIterator[bytes]:
        # 首帧前静默 > interval（模拟沙箱冷启动 + LLM 首 token）→ 心跳应介入。
        await asyncio.sleep(0.12)
        yield b'event: run.completed\ndata: {}\n\n'

    out = await _collect(_with_keepalive(_slow(), interval=0.04))

    assert _SSE_KEEPALIVE_FRAME in out  # 静默期至少插了一个心跳帧
    assert out[-1] == b'event: run.completed\ndata: {}\n\n'  # 上游真帧仍在末尾原样透传


async def test_reraises_upstream_exception() -> None:
    async def _boom() -> AsyncIterator[bytes]:
        await asyncio.sleep(0)
        yield b': warmup\n\n'
        raise RuntimeError('upstream blew up')

    with pytest.raises(RuntimeError, match='upstream blew up'):
        await _collect(_with_keepalive(_boom(), interval=10.0))


async def test_relay_run_stream_unexpected_error_becomes_sse_error_not_decoding_error() -> None:
    # control-plane 抛非 HermesRuntimeError 的未预期异常（inner 的 try 不 catch 它）→ 必须被
    # relay_run_stream 的兜底转成明确 SSE error 帧，绝不逃逸到 StreamingResponse（否则 daemon
    # 只看到 error decoding response body、拿不到真因）。
    runtime_client: Any = AsyncMock()
    runtime_client.start_gateway_by_profile = AsyncMock(side_effect=ValueError('boom upstream'))
    svc = HasnAgentRuntimeDispatchService(runtime_client=runtime_client)

    frames = [chunk async for chunk in svc.relay_run_stream(runtime_profile_id='p1', payload={})]
    body = b''.join(frames).decode()

    assert 'event: error' in body
    assert 'runtime_relay_internal_error' in body
    assert 'boom upstream' in body


async def test_read_only_relay_rejects_old_runtime_before_creating_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧 runtime 缺少只读与幂等契约时，只探测能力，绝不能创建 run。"""

    class _EndpointClient:
        async def start_gateway_by_profile(self, runtime_profile_id: str, trace_id: str | None = None) -> None:
            return None

        async def get_upstream_endpoint(
            self, runtime_profile_id: str, trace_id: str | None = None
        ) -> dict[str, object]:
            return {
                'api_server_host': 'runtime.test',
                'api_server_port': 80,
                'api_server_key': 'secret',
            }

    requests: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == 'GET' and request.url.path == '/v1/capabilities':
            return httpx.Response(status_code=404, json={'detail': 'not found'})
        pytest.fail(f'旧 runtime 不应收到后续请求：{request.method} {request.url.path}')

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(dispatch_module.httpx, 'AsyncClient', _client_factory)
    runtime_client: Any = _EndpointClient()
    service = HasnAgentRuntimeDispatchService(runtime_client=runtime_client)

    frames = [
        chunk
        async for chunk in service._relay_run_stream_inner(
            runtime_profile_id='profile-1',
            payload={
                'dispatch_id': 'dispatch-1',
                'input': '只读回灌',
                'tool_execution': 'disabled',
            },
        )
    ]
    body = b''.join(frames).decode()

    assert requests == [('GET', '/v1/capabilities')]
    assert 'event: error' in body
    assert 'runtime_capability_unsupported' in body


async def test_read_only_relay_negotiates_contract_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新版 runtime 声明完整契约后，才允许创建 run 并中继终态事件。"""

    class _EndpointClient:
        async def start_gateway_by_profile(self, runtime_profile_id: str, trace_id: str | None = None) -> None:
            return None

        async def get_upstream_endpoint(
            self, runtime_profile_id: str, trace_id: str | None = None
        ) -> dict[str, object]:
            return {
                'api_server_host': 'runtime.test',
                'api_server_port': 80,
                'api_server_key': 'secret',
            }

    requests: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == 'GET' and request.url.path == '/v1/capabilities':
            return httpx.Response(
                status_code=200,
                json={
                    'object': 'hermes.api_server.capabilities',
                    'features': {
                        'tool_execution_disabled_v1': True,
                        'dispatch_idempotency_v1': True,
                        'run_terminal_replay_v1': True,
                    },
                },
            )
        if request.method == 'POST' and request.url.path == '/v1/runs':
            return httpx.Response(status_code=200, json={'run_id': 'run-1'})
        if request.method == 'GET' and request.url.path == '/v1/runs/run-1/events':
            return httpx.Response(
                status_code=200,
                content=b'event: run.completed\ndata: {"run_id":"run-1"}\n\n',
                headers={'Content-Type': 'text/event-stream'},
            )
        pytest.fail(f'收到意外请求：{request.method} {request.url.path}')

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs['transport'] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(dispatch_module.httpx, 'AsyncClient', _client_factory)
    runtime_client: Any = _EndpointClient()
    service = HasnAgentRuntimeDispatchService(runtime_client=runtime_client)

    frames = [
        chunk
        async for chunk in service._relay_run_stream_inner(
            runtime_profile_id='profile-1',
            payload={
                'dispatch_id': 'dispatch-1',
                'input': '只读回灌',
                'tool_execution': 'disabled',
            },
        )
    ]

    assert requests == [
        ('GET', '/v1/capabilities'),
        ('POST', '/v1/runs'),
        ('GET', '/v1/runs/run-1/events'),
    ]
    assert b''.join(frames) == b'event: run.completed\ndata: {"run_id":"run-1"}\n\n'
