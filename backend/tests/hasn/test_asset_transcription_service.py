"""asset_transcription_service 单测（09 Stage1g）。

只覆盖：成功解析 text、HTTP 错误/不可达 → 抛错（零 fake，调用方据此标 stt_unavailable）。
转写网络边界用 httpx mock；不依赖真实 STT 服务（Stage 0c 部署后活体验证）。
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.hasn.service.asset_transcription_service import AssetTranscriptionService
from backend.common.exception import errors


@pytest.mark.asyncio
async def test_transcribe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith('/v1/audio/transcriptions')
        assert request.headers['authorization'] == 'Bearer sk-owner'
        return httpx.Response(200, json={'text': '你好世界'})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs.pop('trust_env', None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', _client)
    text = await AssetTranscriptionService.transcribe(b'audio-bytes', owner_token='sk-owner')
    assert text == '你好世界'


@pytest.mark.asyncio
async def test_transcribe_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(503, text='service down'))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **k: real_client(transport=transport, **{x: y for x, y in k.items() if x != 'trust_env'}))
    with pytest.raises(errors.ServerError):
        await AssetTranscriptionService.transcribe(b'audio', owner_token='sk-owner')


@pytest.mark.asyncio
async def test_transcribe_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={'text': ''}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **k: real_client(transport=transport, **{x: y for x, y in k.items() if x != 'trust_env'}))
    with pytest.raises(errors.ServerError):
        await AssetTranscriptionService.transcribe(b'audio', owner_token='sk-owner')
