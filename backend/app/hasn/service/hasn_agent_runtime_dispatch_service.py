"""云端 Runtime 派发代理（双形态 Runtime，设计 08/02 §5 + 实施 01 阶段 B）。

福仔决策①：云端分身的派发链路 = daemon →（BackendGateway agent-scoped, Agent JWT）→
本服务 → huanxing-hermes-runtime(127.0.0.1) → 上游 hermes-agent gateway（Docker 沙箱）。

云端上游 gateway 跑在与后端同机的 127.0.0.1，不对 daemon 暴露公网；因此本服务在云端机上
**复刻 daemon 本地的 C2 派发流程**（gateway/start → upstream_endpoint → POST /v1/runs →
SSE /v1/runs/{run_id}/events），把 SSE 逐帧中继回 daemon。sidecar 仍是纯控制面，数据面
relay → 上游 gateway 直连，一跳，不二次解析 SSE。

零 fake：上游不可达/未 provision 一律以 SSE error 事件如实报，绝不伪造成功。
"""

from __future__ import annotations

import json as jsonlib

from typing import TYPE_CHECKING, Any

import httpx
import sqlalchemy as sa

from backend.app.hasn.model import HasnAgents
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient, HermesRuntimeError
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# 活跃态集合：仅活跃分身可派发（停用/吊销/归档/删除不可）。
_DISPATCHABLE_STATUS = {'active', ''}

# POST /v1/runs 启动一个 run 应快速返回 run_id；SSE events 才是长连接。
_RUN_CREATE_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
# SSE 事件流：read 无超时（流到上游 run.completed/failed/EOF 自然结束），其余有界。
# 对齐 MCP ask 窗口（≥660s），由上游 run 终态/取消收口，不靠 read 超时掐断。
_SSE_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)


def _sse_error(error: str, *, details: str | None = None, status_code: int | None = None) -> bytes:
    payload: dict[str, Any] = {'error': error}
    if details:
        payload['details'] = details
    if status_code is not None:
        payload['status_code'] = status_code
    return f'event: error\ndata: {jsonlib.dumps(payload)}\n\n'.encode()


class HasnAgentRuntimeDispatchService:
    """Agent JWT → 云端 runtime 派发代理（仅 runtime_location=cloud 分身可用）。"""

    def __init__(self, runtime_client: HermesRuntimeClient | None = None) -> None:
        self.runtime_client = runtime_client or HermesRuntimeClient()

    async def resolve_cloud_profile(
        self,
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        runtime_profile_id: str | None,
    ) -> str:
        """校验分身归属 + 云端形态闸门，返回派发用的 runtime_profile_id。

        - 身份恒取自 Agent JWT（agent_hasn_id/owner_hasn_id），不读 body 身份字段；
        - owner 隔离：JWT owner 必须等于分身 owner（防越权派发别 owner 的分身）；
        - 形态闸门：仅 runtime_location=cloud 走此面；local 分身经本地 sidecar 派发，此处拒绝；
        - profile_id 由 daemon（其 binding metadata profile_ref）携带，云端不再二次派生。
        """
        row = (
            await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == agent_hasn_id).limit(1))
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')
        if (row.owner_id or '') != (owner_hasn_id or ''):
            raise errors.ForbiddenError(msg='无权派发该分身')
        location = getattr(row, 'runtime_location', 'local') or 'local'
        if location != 'cloud':
            raise errors.ForbiddenError(
                msg='该分身运行在本地，应经本地 runtime 派发，不走云端派发代理'
            )
        status = getattr(row, 'status', 'active') or 'active'
        if status not in _DISPATCHABLE_STATUS:
            raise errors.ForbiddenError(msg='该分身非活跃状态，不可派发')
        profile_id = (runtime_profile_id or '').strip()
        if not profile_id:
            raise errors.RequestError(msg='runtime_profile_id is required')
        return profile_id

    async def relay_run_stream(
        self,
        *,
        runtime_profile_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """复刻 C2 数据面：gateway/start → upstream_endpoint → POST /v1/runs → SSE 中继。

        本生成器在路由返回 StreamingResponse 后执行——**不得**触碰请求级 db 会话
        （已在 resolve_cloud_profile 阶段取尽所需）。任何控制面/数据面错误都以
        SSE error 事件如实下行，不伪造成功（零 fake）。
        """
        # 控制面：确保上游 gateway 起来 + 拿到它的 host/port/key（同机 127.0.0.1）。
        try:
            await self.runtime_client.start_gateway_by_profile(runtime_profile_id, trace_id=trace_id)
            endpoint = await self.runtime_client.get_upstream_endpoint(runtime_profile_id, trace_id=trace_id)
        except HermesRuntimeError as exc:
            log.warning(f'cloud dispatch control-plane failed for {runtime_profile_id}: {exc}')
            yield _sse_error(exc.error, details=exc.details, status_code=exc.status_code)
            return

        host = endpoint.get('api_server_host')
        port = endpoint.get('api_server_port')
        key = endpoint.get('api_server_key')
        if not host or not port or not key:
            yield _sse_error('upstream_endpoint_unconfigured', details='missing api_server_{host,port,key}')
            return
        runs_create_path = endpoint.get('runs_create_path') or '/v1/runs'
        events_path_template = endpoint.get('runs_events_path_template') or '/v1/runs/{run_id}/events'
        base_url = f'http://{host}:{int(port)}'
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        run_body = dict(payload or {})
        run_body['stream'] = True

        try:
            async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
                # 数据面 1：POST /v1/runs 启动 run（短超时，应快速返回 run_id）。
                resp = await client.post(
                    f'{base_url}{runs_create_path}', json=run_body, headers=headers, timeout=_RUN_CREATE_TIMEOUT
                )
                if resp.status_code >= 400:
                    yield _sse_error('runtime_run_rejected', details=resp.text, status_code=resp.status_code)
                    return
                try:
                    run = resp.json()
                except ValueError:
                    yield _sse_error('runtime_run_bad_response', details=resp.text)
                    return
                run_id = run.get('run_id') if isinstance(run, dict) else None
                if not run_id:
                    yield _sse_error('runtime_run_missing_run_id', details='upstream /v1/runs response missing run_id')
                    return

                # 数据面 2：SSE GET events → 逐帧中继（read 无超时，由上游终态收口）。
                events_path = events_path_template.replace(f'{run_id}', str(run_id))
                async with client.stream('GET', f'{base_url}{events_path}', headers=headers) as events:
                    if events.status_code >= 400:
                        body = await events.aread()
                        yield _sse_error(
                            'runtime_events_rejected', details=body.decode('utf-8', 'replace'),
                            status_code=events.status_code,
                        )
                        return
                    async for chunk in events.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            log.warning(f'cloud dispatch data-plane failed for {runtime_profile_id}: {exc}')
            yield _sse_error('runtime_unavailable', details=str(exc))

    async def cancel_run(
        self,
        *,
        runtime_profile_id: str,
        run_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """取消一个进行中的 run：解析上游端点 → POST /v1/runs/{run_id}/cancel。"""
        endpoint = await self.runtime_client.get_upstream_endpoint(runtime_profile_id, trace_id=trace_id)
        host = endpoint.get('api_server_host')
        port = endpoint.get('api_server_port')
        key = endpoint.get('api_server_key')
        if not host or not port or not key:
            raise HermesRuntimeError(error='upstream_endpoint_unconfigured', trace_id=trace_id)
        cancel_template = endpoint.get('runs_cancel_path_template') or f'/v1/runs/{run_id}/cancel'
        cancel_path = cancel_template.replace(f'{run_id}', str(run_id))
        base_url = f'http://{host}:{int(port)}'
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        try:
            async with httpx.AsyncClient(timeout=_RUN_CREATE_TIMEOUT) as client:
                resp = await client.post(f'{base_url}{cancel_path}', json={}, headers=headers)
        except httpx.HTTPError as exc:
            raise HermesRuntimeError(error='runtime_unavailable', details=str(exc), trace_id=trace_id) from exc
        cancelled = False
        if resp.status_code < 400:
            try:
                data = resp.json()
                cancelled = bool(data.get('cancelled')) if isinstance(data, dict) else False
            except ValueError:
                cancelled = False
        return {'run_id': run_id, 'cancelled': cancelled}


hasn_agent_runtime_dispatch_service = HasnAgentRuntimeDispatchService()
