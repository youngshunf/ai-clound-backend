from __future__ import annotations

import asyncio
import random

from collections.abc import Awaitable, Callable
from typing import Any

import httpx


DEFAULT_FIRECRAWL_BASE_URL = 'https://firecrawl.dcfuture.com.cn'
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FirecrawlTransportError(Exception):
    pass


class FirecrawlHTTPError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


Sender = Callable[[str, str, dict[str, Any], dict[str, str], float], Awaitable[dict[str, Any]]]


class FirecrawlClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_FIRECRAWL_BASE_URL,
        api_key: str | None = None,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        sender: Sender | None = None,
        sleep: Callable[[float], Any] | None = None,
        jitter: Callable[[], float] | None = None,
    ):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._sender = sender or self._httpx_sender
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.random

    async def scrape_markdown(self, url: str) -> dict[str, Any]:
        payload = {'url': url, 'formats': ['markdown', 'html'], 'onlyMainContent': True}
        return await self._post('/v1/scrape', payload, extract_mode='scrape_markdown')

    async def scrape_lead_json(self, url: str, schema_version: str, prompt_version: str) -> dict[str, Any]:
        payload = {
            'url': url,
            'formats': ['markdown', 'html', 'json'],
            'jsonOptions': {'schema': lead_json_schema(), 'prompt': lead_prompt(prompt_version)},
            'onlyMainContent': True,
        }
        result = await self._post('/v1/scrape', payload, extract_mode='scrape_json')
        result['llm_schema_version'] = schema_version
        result['llm_prompt_version'] = prompt_version
        return result

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        payload = {'query': query, 'limit': limit}
        result = await self._post('/v1/search', payload, extract_mode='search')
        raw_results = result.get('raw_payload') if isinstance(result.get('raw_payload'), list) else []
        return [normalized for item in raw_results if (normalized := _normalize_search_result(item))]

    async def extract_leads(self, urls: list[str], schema_version: str, prompt_version: str) -> dict[str, Any]:
        payload = {'urls': urls, 'schema': lead_json_schema(), 'prompt': lead_prompt(prompt_version)}
        result = await self._post('/v1/extract', payload, extract_mode='extract')
        result['llm_schema_version'] = schema_version
        result['llm_prompt_version'] = prompt_version
        return result

    async def _post(self, endpoint: str, payload: dict[str, Any], *, extract_mode: str) -> dict[str, Any]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for attempt_index in range(attempts):
            try:
                response = await self._sender(
                    'POST',
                    f'{self.base_url}{endpoint}',
                    payload,
                    headers,
                    self.timeout_seconds,
                )
                status_code = int(response.get('status_code', 200))
                body = response.get('json') or {}
                if status_code >= 400:
                    if status_code in RETRYABLE_STATUS and attempt_index < attempts - 1:
                        await self._backoff(attempt_index)
                        continue
                    raise FirecrawlHTTPError(status_code, str(body.get('error') or body.get('message') or status_code))
                return self._normalize_response(body, extract_mode=extract_mode, attempt_count=attempt_index + 1)
            except (FirecrawlTransportError, httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_error = exc
                if attempt_index >= attempts - 1:
                    raise FirecrawlTransportError(str(exc)) from exc
                await self._backoff(attempt_index)
        raise FirecrawlTransportError(str(last_error or 'firecrawl request failed'))

    async def _backoff(self, attempt_index: int) -> None:
        delay = min(2**attempt_index + self._jitter(), 30)
        result = self._sleep(delay)
        if hasattr(result, '__await__'):
            await result

    async def _httpx_sender(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {'text': response.text}
        return {'status_code': response.status_code, 'json': body}

    def _normalize_response(self, body: dict[str, Any], *, extract_mode: str, attempt_count: int) -> dict[str, Any]:
        data = body.get('data') if isinstance(body.get('data'), dict | list) else body
        if isinstance(data, list):
            return {
                'source_url': None,
                'title': None,
                'markdown': None,
                'raw_html': None,
                'raw_text': None,
                'raw_payload': data,
                'structured_payload': None,
                'llm_confidence': None,
                'extract_mode': extract_mode,
                'attempt_count': attempt_count,
            }
        metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        structured_payload = data.get('json') or data.get('extract') or data.get('structured_payload')
        if structured_payload is None and extract_mode == 'extract':
            structured_payload = data
        return {
            'source_url': data.get('url') or metadata.get('sourceURL'),
            'title': data.get('title') or metadata.get('title'),
            'markdown': data.get('markdown'),
            'raw_html': data.get('html'),
            'raw_text': data.get('text'),
            'raw_payload': data,
            'structured_payload': structured_payload,
            'llm_confidence': data.get('confidence'),
            'extract_mode': extract_mode,
            'attempt_count': attempt_count,
        }


def lead_json_schema() -> dict[str, Any]:
    return {
        'type': 'object',
        'properties': {
            'company_name': {'type': 'string'},
            'contact_name': {'type': 'string'},
            'emails': {'type': 'array', 'items': {'type': 'string'}},
            'phones': {'type': 'array', 'items': {'type': 'string'}},
            'website': {'type': 'string'},
            'address': {'type': 'string'},
            'industry': {'type': 'string'},
            'description': {'type': 'string'},
        },
    }


def lead_prompt(prompt_version: str) -> str:
    return f'{prompt_version}: only extract business contact information explicitly visible on the page.'


def _normalize_search_result(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    url = item.get('url') or item.get('sourceURL') or metadata.get('sourceURL')
    if not url:
        return None
    return {'url': url, 'title': item.get('title') or metadata.get('title'), 'raw_payload': item}
