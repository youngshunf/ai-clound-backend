"""云端语音转写服务（09 Stage1g / 0c）。

STT 走 new-api 背后的 OpenAI 兼容 audio relay（faster-whisper），统一 owner token 计费/日志，
与「LLM 都走 new-api」一致。base 默认 llm.dcfuture.cn，可经 NEWAPI_AUDIO_BASE_URL 覆盖。

零 fake：转写不可用（服务未部署/网络失败/非 2xx）→ 抛错，调用方据此把资产标 extract_status='stt_unavailable'，
绝不编造 transcript。完整启用依赖 Stage 0c（部署 faster-whisper 服务 + new-api 开 audio relay）。

注意：CPU 上 large-v3 转写需数秒~数十秒，不要阻塞用户上传同步路径——
语音上传注册为 extract_status='pending'，由异步 worker 或 runtime ingest（3d）转写。
"""

from __future__ import annotations

import os

import httpx

from backend.common.exception import errors
from backend.common.log import log

_DEFAULT_BASE = 'https://llm.dcfuture.cn'
_STT_MODEL = os.getenv('NEWAPI_STT_MODEL', 'whisper-1')


def _audio_base_url() -> str:
    return os.getenv('NEWAPI_AUDIO_BASE_URL', _DEFAULT_BASE).rstrip('/')


class AssetTranscriptionService:
    @staticmethod
    async def transcribe(
        audio: bytes,
        *,
        owner_token: str,
        filename: str = 'audio.webm',
        content_type: str = 'audio/webm',
        timeout: float = 120.0,
    ) -> str:
        """调 {new-api}/v1/audio/transcriptions 转写音频，返回 transcript 文本。失败抛错（零 fake）。"""
        url = f'{_audio_base_url()}/v1/audio/transcriptions'
        files = {'file': (filename, audio, content_type)}
        data = {'model': _STT_MODEL}
        headers = {'Authorization': f'Bearer {owner_token}'}
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(url, files=files, data=data, headers=headers)
                resp.raise_for_status()
                text = resp.json().get('text', '')
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:300] if e.response.text else e.response.reason_phrase
            raise errors.ServerError(msg=f'语音转写失败: HTTP {e.response.status_code} {detail}')
        except Exception as e:
            log.warning(f'语音转写不可用: {type(e).__name__}: {e!r}')
            raise errors.ServerError(msg=f'语音转写不可用: {type(e).__name__}')
        if not text:
            raise errors.ServerError(msg='语音转写返回空文本')
        return text


asset_transcription_service: AssetTranscriptionService = AssetTranscriptionService()
