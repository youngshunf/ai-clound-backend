"""出站 MCP client（remote_service：streamable HTTP + SSE）—— P7 事实源 10 §3/§6/§6.1。

范式（qcc 实测，决策 §3）：
  initialize(protocolVersion 2025-03-26) → 取 Mcp-Session-Id →
  notifications/initialized → tools/list / tools/call；
  响应可为 application/json 或 text/event-stream（SSE，解析 data: 行累积 JSON-RPC）。

连接生命周期（10 §6.1 远程型）：**按需请求、无协议级长连接**——每次调用一次 HTTP 请求；
`Mcp-Session-Id` 是逻辑会话，可短期复用（本 client 单次 introspect/call 内复用），失效即重 initialize。
不挂 GET SSE 长连接（放弃 server→client 主动推送，工具目录靠定时 re-probe 维护）。

**`trust_env=False`**（§3 + 实测）：macOS/部署环境系统代理会劫持 localhost/SaaS 端点致空 body 503。
"""

from __future__ import annotations

import json
import logging

from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = '2025-03-26'
_CLIENT_INFO = {'name': 'huanxing-mcp-gateway', 'version': '1.0.0'}
_DEFAULT_TIMEOUT = 30.0


class ExternalMcpClientError(RuntimeError):
    """出站 MCP 调用失败（连接 / 协议 / 第三方 error）。"""


@dataclass
class _Rpc:
    """JSON-RPC id 计数器。"""

    _next: int = field(default=1)

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


def _parse_sse_payload(text: str) -> dict[str, Any] | None:
    """从 SSE 文本里抽出**最后一个** data: 的 JSON 负载（JSON-RPC 响应）。"""
    last: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith('data:'):
            continue
        data = line[len('data:'):].strip()
        if not data or data == '[DONE]':
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last = parsed
    return last


def _extract_rpc_result(response: httpx.Response) -> dict[str, Any]:
    """从 HTTP 响应（JSON 或 SSE）抽出 JSON-RPC result，error 则抛。"""
    content_type = response.headers.get('content-type', '')
    payload: dict[str, Any] | None
    if 'text/event-stream' in content_type:
        payload = _parse_sse_payload(response.text)
    else:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = _parse_sse_payload(response.text)
    if not isinstance(payload, dict):
        raise ExternalMcpClientError(
            f'第三方 MCP 响应无法解析（status={response.status_code} ct={content_type}）'
        )
    if payload.get('error'):
        err = payload['error']
        msg = err.get('message') if isinstance(err, dict) else str(err)
        raise ExternalMcpClientError(f'第三方 MCP 返回 error: {msg}')
    result = payload.get('result')
    if result is None:
        raise ExternalMcpClientError('第三方 MCP 响应缺少 result')
    return result


class RemoteMcpClient:
    """远程服务型第三方 MCP 的出站客户端（单 endpoint，单次会话内复用 session id）。

    `headers` 已是**解析后的明文头**（含注入的 Bearer，由网关解析 secret:// 得到，绝不日志）。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._endpoint = endpoint
        self._base_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            **(headers or {}),
        }
        self._timeout = timeout
        self._session_id: str | None = None
        self._rpc = _Rpc()

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self._base_headers)
        if self._session_id:
            headers['Mcp-Session-Id'] = self._session_id
        return headers

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        resp = await client.post(self._endpoint, json=body, headers=self._request_headers())
        # initialize 响应回带 session id（大小写不敏感）。
        session_id = resp.headers.get('mcp-session-id') or resp.headers.get('Mcp-Session-Id')
        if session_id:
            self._session_id = session_id
        return resp

    async def _initialize(self, client: httpx.AsyncClient) -> None:
        body = {
            'jsonrpc': '2.0',
            'id': self._rpc.take(),
            'method': 'initialize',
            'params': {
                'protocolVersion': _MCP_PROTOCOL_VERSION,
                'capabilities': {},
                'clientInfo': _CLIENT_INFO,
            },
        }
        resp = await self._post(client, body)
        if resp.status_code >= 400:
            raise ExternalMcpClientError(f'initialize 失败 status={resp.status_code}: {resp.text[:200]}')
        _extract_rpc_result(resp)
        # 通知 initialized（无响应；部分 server 要求）。
        try:
            await self._post(
                client,
                {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}},
            )
        except httpx.HTTPError:
            logger.debug('notifications/initialized 发送失败（可忽略）')

    async def list_tools(self) -> list[dict[str, Any]]:
        """initialize → tools/list（含分页 nextCursor），返回第三方 advertised tools 原始列表。"""
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            await self._initialize(client)
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(20):  # 分页安全上限
                params: dict[str, Any] = {}
                if cursor:
                    params['cursor'] = cursor
                resp = await self._post(
                    client,
                    {'jsonrpc': '2.0', 'id': self._rpc.take(), 'method': 'tools/list', 'params': params},
                )
                if resp.status_code >= 400:
                    raise ExternalMcpClientError(f'tools/list 失败 status={resp.status_code}: {resp.text[:200]}')
                result = _extract_rpc_result(resp)
                page = result.get('tools') or []
                tools.extend(t for t in page if isinstance(t, dict))
                cursor = result.get('nextCursor')
                if not cursor:
                    break
            return tools

    async def call_tool(self, raw_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """initialize → tools/call，返回第三方 result（含 content / structuredContent / isError）。"""
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            await self._initialize(client)
            resp = await self._post(
                client,
                {
                    'jsonrpc': '2.0',
                    'id': self._rpc.take(),
                    'method': 'tools/call',
                    'params': {'name': raw_name, 'arguments': arguments or {}},
                },
            )
            if resp.status_code >= 400:
                raise ExternalMcpClientError(f'tools/call 失败 status={resp.status_code}: {resp.text[:200]}')
            return _extract_rpc_result(resp)
