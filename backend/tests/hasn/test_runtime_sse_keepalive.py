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

from typing import TYPE_CHECKING

import pytest

from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    _SSE_KEEPALIVE_FRAME,
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
