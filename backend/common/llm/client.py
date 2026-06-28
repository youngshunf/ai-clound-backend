"""统一 LLM chat completion 客户端（OpenAI 兼容，默认走 new-api 网关）。

收敛自 ``marketplace/translation_service`` 中经生产验证的传输内核（stream + SSE 解析、
瞬时错误重试退避、模型 fallback），并补齐 ``trust_env=False`` 与可配置的 base_url/key/model，
供全后端各业务复用，消灭散落各处的 ``httpx.post('/chat/completions')``。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from backend.common.log import log
from backend.core.conf import settings

# 瞬时网关错误（可重试）：超时/过早/限流/上游 5xx。
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_MAX_TOKENS = 1000
_MAX_ATTEMPTS = 3
_FALLBACK_MODEL_NAME = 'gpt-5.5'  # settings 未配默认模型时的兜底名（与 conf.py LLM_DEFAULT_MODEL 默认一致）


class LLMError(Exception):
    """LLM chat completion 最终失败（穷尽模型与重试，或不可重试错误）时抛出。

    best-effort 调用方应自行 ``try/except LLMError`` 并降级；不要让它静默吞掉。
    """


class LLMChatClient:
    """OpenAI 兼容 chat completion 客户端。

    默认从 ``settings`` 取 new-api 网关（``LLM_API_BASE_URL`` / ``LLM_API_KEY`` /
    ``LLM_DEFAULT_MODEL``）；也可在构造时覆盖 base_url/api_key/model（如 article_summary
    走平台代理、translation 走 TRANSLATION_MODEL）。``complete()`` 返回消息正文，失败抛
    :class:`LLMError`。

    用法::

        from backend.common.llm import llm_client
        text = await llm_client.complete(messages, model='agnes-2.0-flash', temperature=0)
        data = await llm_client.complete_json(messages)  # response_format=json_object + 解析
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        models: list[str] | None = None,
        fallback_model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url_override = base_url
        self._api_key_override = api_key
        self._default_model = model
        self._default_models = models  # 实例级默认 failover 链覆盖（优先于 settings.LLM_DEFAULT_MODELS）
        self._fallback_model = fallback_model
        self._default_timeout = timeout
        self._transport = transport  # 仅测试注入；生产为 None（默认 trust_env=False 客户端）

    # ---- 配置解析（默认走 new-api 网关）----
    def _raw_base_url(self) -> str:
        raw = self._base_url_override if self._base_url_override is not None else (settings.LLM_API_BASE_URL or '')
        return (raw or '').strip().rstrip('/')

    @property
    def base_url(self) -> str:
        raw = self._raw_base_url()
        if not raw:
            raise LLMError('LLM 网关 base_url 未配置（settings.LLM_API_BASE_URL 为空）')
        return raw if raw.endswith('/v1') else f'{raw}/v1'

    @property
    def api_key(self) -> str:
        key = self._api_key_override if self._api_key_override is not None else settings.LLM_API_KEY
        return (key or '').strip()

    @property
    def is_configured(self) -> bool:
        """网关地址与 key 是否齐备（任一为空时调用方应跳过 LLM、走兜底）。"""
        return bool(self._raw_base_url()) and bool(self.api_key)

    def _model_chain(self, model: str | None, models: list[str] | None) -> list[str]:
        if models:
            chain = [m for m in models if m]
            if chain:
                return chain
        if model:
            return [model]  # 显式 per-call 模型 → 就用它（不叠 instance fallback）
        return self._default_model_chain()

    def _default_model_chain(self) -> list[str]:
        """无 per-call 模型时的默认 failover 链。

        优先级（实例显式配置永远赢过全局默认，避免给 translation/article_summary 等指定了
        ``model=`` 的实例被全局链覆盖）：
        1. 实例 ``models``（显式整条链）
        2. 实例 ``model``（+ 可选 ``fallback_model``）—— 构造方明确选的单模型/对
        3. ``settings.LLM_DEFAULT_MODELS``（全局默认 failover 链，逐模型自动切换）—— 模块单例
           ``llm_client`` 走这条，让 owner 记忆合并 / 画像判定等后端任务默认就带 failover
        4. ``settings.LLM_DEFAULT_MODEL`` / 兜底名
        """
        if self._default_models:
            chain = [m.strip() for m in self._default_models if m and m.strip()]
            if chain:
                return chain
        if self._default_model:
            chain = [self._default_model]
            if self._fallback_model and self._fallback_model != self._default_model:
                chain.append(self._fallback_model)
            return chain
        configured = [m.strip() for m in (settings.LLM_DEFAULT_MODELS or []) if m and m.strip()]
        if configured:
            return configured
        return [settings.LLM_DEFAULT_MODEL or _FALLBACK_MODEL_NAME]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        models: list[str] | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
        stream: bool = True,
    ) -> str:
        """调 chat completion，返回消息正文；失败抛 :class:`LLMError`。

        默认 ``stream=True``：部分 new-api 网关渠道在 stream=false 时只回空 chunk
        (completion_tokens=0)，stream=true 才出内容；解析层同时兼容 SSE 与普通 JSON 响应。
        """
        base_url = self.base_url
        api_key = self.api_key
        model_chain = self._model_chain(model, models)
        base_payload: dict[str, Any] = {'messages': messages, 'max_tokens': max_tokens, 'stream': stream}
        if temperature is not None:
            base_payload['temperature'] = temperature
        request_timeout = timeout if timeout is not None else (self._default_timeout or _DEFAULT_TIMEOUT)

        async with self._http_client(request_timeout) as client:
            last_error: str | None = None
            for model_index, model_name in enumerate(model_chain):
                payload = {**base_payload, 'model': model_name}
                if response_format:
                    payload['response_format'] = response_format
                try:
                    for attempt in range(_MAX_ATTEMPTS):
                        content = await self._post(client, base_url, api_key, payload, model_name, attempt)
                        if content:
                            return content
                        if attempt < _MAX_ATTEMPTS - 1:
                            await asyncio.sleep(2.0 * (attempt + 1))
                    # fallback 模型上若带 response_format 仍失败，放宽一次（部分模型不支持 json mode）。
                    if model_index > 0 and response_format:
                        content = await self._post(
                            client, base_url, api_key, {**base_payload, 'model': model_name}, model_name, _MAX_ATTEMPTS
                        )
                        if content:
                            return content
                    last_error = f'{model_name}: 空内容'
                except LLMError as exc:
                    # 该模型硬错误（非瞬时 4xx：model_not_found / 余额不足 / 鉴权等）→ 记录并切下一个
                    # 模型（failover：「一个模型挂了，自动切换下一个」），整条链穷尽才抛。
                    last_error = f'{model_name}: {exc}'
                    log.warning(f'LLM 模型 {model_name} 失败，切换下一个: {exc}')
                    continue

        raise LLMError(f'LLM 穷尽模型链仍失败（{last_error or "空内容"}）')

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        models: list[str] | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """便捷：``response_format=json_object`` 调用并把返回解析为 dict（容错去 code fence）。"""
        raw = await self.complete(
            messages,
            model=model,
            models=models,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={'type': 'json_object'},
            timeout=timeout,
        )
        return self._parse_json_object(raw)

    # ---- 传输 ----
    def _http_client(self, timeout: float) -> httpx.AsyncClient:
        # trust_env=False：绕开 macOS dev 系统代理对 localhost new-api 的劫持（空 body 503）。
        kwargs: dict[str, Any] = {'timeout': timeout, 'trust_env': False}
        if self._transport is not None:
            kwargs['transport'] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _post(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        model: str,
        attempt: int,
    ) -> str | None:
        """POST 一次；成功返回正文，瞬时错误/空内容返回 None（让调用方重试），硬错误抛。"""
        response = await client.post(
            f'{base_url}/chat/completions',
            json=payload,
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        )
        if response.status_code != 200:
            log.error(f'LLM API error: {response.status_code} - {response.text[:500]}')
            if response.status_code in _TRANSIENT_STATUS:
                return None
            raise LLMError(f'LLM API error: {response.status_code}')

        content_type = response.headers.get('content-type', '')
        result = (
            self._parse_sse(response.text)
            if 'text/event-stream' in content_type
            else response.json()
        )
        content = self._extract_content(result)
        if content:
            return content
        log.warning(f'LLM 返回空内容 model={model} attempt={attempt + 1}: {str(result)[:300]}')
        return None

    @staticmethod
    def _extract_content(result: dict[str, Any]) -> str | None:
        """从 choices[].message.content / delta.content 拼接正文（兼容普通响应与 SSE 增量）。"""
        choices = result.get('choices')
        if not isinstance(choices, list) or not choices:
            return None
        chunks: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get('message')
            if isinstance(message, dict) and message.get('content'):
                chunks.append(str(message['content']))
            delta = choice.get('delta')
            if isinstance(delta, dict) and delta.get('content'):
                chunks.append(str(delta['content']))
        content = ''.join(chunks).strip()
        return content or None

    @classmethod
    def _parse_sse(cls, text: str) -> dict[str, Any]:
        """把 SSE 流（``data: {...}`` 行）归并成单个 ``{choices, usage}`` 响应字典。"""
        choices: list[dict[str, Any]] = []
        usage: dict[str, Any] | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith('data:'):
                continue
            payload = line.removeprefix('data:').strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get('usage'):
                usage = chunk['usage']
            for choice in chunk.get('choices') or []:
                if isinstance(choice, dict):
                    choices.append(choice)
        return {'choices': choices, 'usage': usage}

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        """把 LLM 文本解析为 JSON 对象：去 ```json``` code fence、截取首个 ``{`` 到末个 ``}``。"""
        content = (raw or '').strip()
        if content.startswith('```'):
            content = re.sub(r'\A```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```\Z', '', content)
        start = content.find('{')
        end = content.rfind('}')
        if start == -1 or end == -1 or end < start:
            raise LLMError('LLM 返回不是 JSON 对象')
        data = json.loads(content[start : end + 1])
        if not isinstance(data, dict):
            raise LLMError('LLM 返回 JSON 不是对象')
        return data


# 模块级单例：默认 new-api 网关。业务直接 ``from backend.common.llm import llm_client``。
llm_client = LLMChatClient()
