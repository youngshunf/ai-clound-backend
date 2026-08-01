from __future__ import annotations

import json as jsonlib

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from backend.common.service_registry import service_endpoint
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(slots=True)
class HermesRuntimeError(Exception):
    """Structured runtime error safe to return in response_base.data."""

    error: str
    details: str | None = None
    action: str | None = None
    trace_id: str | None = None
    status_code: int | None = None

    def __str__(self) -> str:
        return self.details or self.error

    def to_response_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {'error': self.error}
        if self.details:
            data['details'] = self.details
        if self.action:
            data['action'] = self.action
        if self.trace_id:
            data['trace_id'] = self.trace_id
        return data


class HermesRuntimeClient:
    """HTTP client for huanxing-hermes-runtime /runtime/v1/agents APIs."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        # 连接三元组经统一服务目录解析（env HUANXING_HERMES_RUNTIME_* → settings → services.toml
        # [service.hermes] → dev 本机回落 127.0.0.1:8765）。token 不派生（derive_token=False，固定服务级
        # Bearer）。HUANXING_HERMES_RUNTIME_ID 与 RUNTIME_INTERNAL_TOKEN 是非连接配置，仍读 settings。
        ep = service_endpoint('hermes')
        configured_base_url = base_url or ep.base_url or getattr(settings, 'HUANXING_HERMES_RUNTIME_BASE_URL', '') or ''
        self.base_url = configured_base_url.rstrip('/')
        self.api_token = (
            api_token
            if api_token is not None
            else (ep.token or getattr(settings, 'HUANXING_HERMES_RUNTIME_API_TOKEN', ''))
        )
        self.timeout_seconds = (
            timeout_seconds or ep.timeout or getattr(settings, 'HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS', 10.0)
        )

    def _headers(self, trace_id: str | None = None) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_token:
            headers['Authorization'] = f'Bearer {self.api_token}'
        if trace_id:
            headers['X-Huanxing-Request-Id'] = trace_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        trace_id: str | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise HermesRuntimeError(
                error='runtime_unavailable',
                details='HUANXING_HERMES_RUNTIME_BASE_URL is not configured',
                action='configure runtime base url',
                trace_id=trace_id,
            )
        url = f'{self.base_url}{path}'
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, url, headers=self._headers(trace_id), json=json, params=params)
        except httpx.TimeoutException as exc:
            raise HermesRuntimeError(
                error='runtime_unavailable', details='connect timeout', action='retry later', trace_id=trace_id
            ) from exc
        except httpx.HTTPError as exc:
            raise HermesRuntimeError(
                error='runtime_unavailable', details=str(exc), action='retry later', trace_id=trace_id
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {'details': response.text}

        if response.status_code >= 400:
            if isinstance(body, dict):
                error = str(body.get('error') or 'runtime_error')
                details = body.get('details') or body.get('message') or response.text
                action = body.get('action')
            else:
                error = 'runtime_error'
                details = str(body)
                action = None
            raise HermesRuntimeError(
                error=error,
                details=str(details) if details is not None else None,
                action=str(action) if action else None,
                trace_id=trace_id,
                status_code=response.status_code,
            )
        return body

    async def ensure_agent(self, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', '/runtime/v1/agents', json=payload, trace_id=trace_id)

    async def get_agent(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}', trace_id=trace_id)

    async def delete_agent(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('DELETE', f'/runtime/v1/agents/{runtime_profile_id}', trace_id=trace_id)

    async def get_soul(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/soul', trace_id=trace_id)

    async def put_soul(self, runtime_profile_id: str, content: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('PUT', f'/runtime/v1/agents/{runtime_profile_id}/soul', json={'content': content}, trace_id=trace_id)

    async def get_user_profile(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/user-profile', trace_id=trace_id)

    async def put_user_profile(self, runtime_profile_id: str, content: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('PUT', f'/runtime/v1/agents/{runtime_profile_id}/user-profile', json={'content': content}, trace_id=trace_id)

    async def get_gateway_status(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/gateway/status', trace_id=trace_id)

    async def start_gateway(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/gateway/start', json={}, trace_id=trace_id)

    async def stop_gateway(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/gateway/stop', json={}, trace_id=trace_id)

    async def restart_gateway(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/gateway/restart', json={}, trace_id=trace_id)

    async def get_gateway_logs(self, runtime_profile_id: str, limit: int = 100, trace_id: str | None = None) -> Any:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/gateway/logs', params={'limit': limit}, trace_id=trace_id)

    async def get_workspace_status(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/workspace/status', trace_id=trace_id)

    async def get_channels(self, runtime_profile_id: str, trace_id: str | None = None) -> Any:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/channels', trace_id=trace_id)

    async def start_channel_qr(self, runtime_profile_id: str, channel: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/channels/{channel}/qr/start', json=payload, trace_id=trace_id)

    async def get_channel_qr_status(self, runtime_profile_id: str, channel: str, session_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/channels/{channel}/qr/{session_id}/status', trace_id=trace_id)

    async def manual_channel(self, runtime_profile_id: str, channel: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/channels/{channel}/manual', json=payload, trace_id=trace_id)

    async def test_channel(self, runtime_profile_id: str, channel: str, payload: dict[str, Any] | None = None, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/channels/{channel}/test', json=payload or {}, trace_id=trace_id)

    async def unbind_channel(self, runtime_profile_id: str, channel: str, payload: dict[str, Any] | None = None, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/channels/{channel}/unbind', json=payload or {}, trace_id=trace_id)

    async def chat_completions(self, runtime_profile_id: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/chat/completions', json=payload, trace_id=trace_id)

    async def get_chat_history(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/chat/history', trace_id=trace_id)

    async def create_run(self, runtime_profile_id: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        return await self._request('POST', f'/runtime/v1/agents/{runtime_profile_id}/runs', json=payload, trace_id=trace_id)

    async def get_run_events(self, runtime_profile_id: str, run_id: str, trace_id: str | None = None) -> Any:
        return await self._request('GET', f'/runtime/v1/agents/{runtime_profile_id}/runs/{run_id}/events', trace_id=trace_id)

    # 已退役（H8）：`probe` / `list_skills` / `read_skill` / `enable_skill` / `disable_skill` /
    # `start_gateway_by_profile` / `get_upstream_endpoint` 是「分身跑在云端沙箱」形态专用的
    # 控制面方法（唯一调用方是已删除的云端 Runtime 派发代理），随该形态一并删除。本类其余
    # 方法归 `/api/v1/hermes/app/*` 应用面所有，与云端 Runtime 形态无关，保留不动。

    async def apply_template(
        self,
        runtime_profile_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            'POST',
            f'/runtime/v1/agents/{runtime_profile_id}/template/apply',
            json=payload,
            trace_id=trace_id,
        )

    async def get_template_status(
        self,
        runtime_profile_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            'GET',
            f'/runtime/v1/agents/{runtime_profile_id}/template/status',
            trace_id=trace_id,
        )

    async def install_credential(
        self,
        runtime_profile_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            'POST',
            f'/runtime/v1/agents/{runtime_profile_id}/credential/install',
            json=payload,
            trace_id=trace_id,
        )

    async def uninstall_credential(
        self,
        runtime_profile_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            'DELETE',
            f'/runtime/v1/agents/{runtime_profile_id}/credential',
            trace_id=trace_id,
        )

    async def chat_completions_stream(
        self,
        runtime_profile_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        if not self.base_url:
            err_payload = jsonlib.dumps({'error': 'runtime_unavailable'})
            yield f'event: error\ndata: {err_payload}\n\n'.encode()
            return
        body = dict(payload)
        body['stream'] = True
        url = f'{self.base_url}/runtime/v1/agents/{runtime_profile_id}/chat/completions'
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    'POST', url, headers=self._headers(trace_id), json=body
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        try:
                            data = jsonlib.loads(text or b'{}')
                            error_code = str(data.get('error') or 'runtime_error') if isinstance(data, dict) else 'runtime_error'
                        except ValueError:
                            error_code = 'runtime_error'
                        err_payload = jsonlib.dumps({'error': error_code, 'status_code': response.status_code})
                        yield f'event: error\ndata: {err_payload}\n\n'.encode()
                        return
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            err_payload = jsonlib.dumps({'error': 'runtime_unavailable', 'details': str(exc)})
            yield f'event: error\ndata: {err_payload}\n\n'.encode()
