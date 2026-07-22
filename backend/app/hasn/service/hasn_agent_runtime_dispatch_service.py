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

import asyncio
import contextlib
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
_UPSTREAM_CAPABILITIES_PATH = '/v1/capabilities'
_READ_ONLY_RUN_CAPABILITIES = (
    'tool_execution_disabled_v1',
    'dispatch_idempotency_v1',
    'run_terminal_replay_v1',
)


def _sse_error(error: str, *, details: str | None = None, status_code: int | None = None) -> bytes:
    payload: dict[str, Any] = {'error': error}
    if details:
        payload['details'] = details
    if status_code is not None:
        payload['status_code'] = status_code
    return f'event: error\ndata: {jsonlib.dumps(payload)}\n\n'.encode()


def _read_only_run_capability_error(capabilities: Any) -> str | None:
    """校验只读回灌依赖的完整上游契约，返回稳定错误详情。"""
    if not isinstance(capabilities, dict):
        return 'read_only_run_contract_v1: capabilities must be an object'
    if capabilities.get('object') != 'hermes.api_server.capabilities':
        return 'read_only_run_contract_v1: invalid capabilities object'
    features = capabilities.get('features')
    if not isinstance(features, dict):
        return 'read_only_run_contract_v1: missing features'
    missing = [name for name in _READ_ONLY_RUN_CAPABILITIES if features.get(name) is not True]
    if missing:
        return f'read_only_run_contract_v1: missing or disabled {",".join(missing)}'
    return None


async def _negotiate_read_only_run_contract(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
) -> bytes | None:
    """协商只读回灌契约；成功返回空，失败返回可直接下行的 SSE 错误帧。"""
    response = await client.get(
        f'{base_url}{_UPSTREAM_CAPABILITIES_PATH}',
        headers=headers,
        timeout=_RUN_CREATE_TIMEOUT,
    )
    if response.status_code >= 400:
        return _sse_error(
            'runtime_capability_unsupported',
            details=f'read_only_run_contract_v1: capabilities HTTP {response.status_code}',
            status_code=response.status_code,
        )
    try:
        capabilities = response.json()
    except ValueError:
        return _sse_error(
            'runtime_capability_unsupported',
            details='read_only_run_contract_v1: invalid capabilities JSON',
        )
    capability_error = _read_only_run_capability_error(capabilities)
    if capability_error is None:
        return None
    return _sse_error(
        'runtime_capability_unsupported',
        details=capability_error,
    )


async def _create_runtime_run(
    client: httpx.AsyncClient,
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[str | None, bytes | None]:
    """创建上游 run，并把协议错误收敛为可下行的 SSE 错误帧。"""
    response = await client.post(url, json=body, headers=headers, timeout=_RUN_CREATE_TIMEOUT)
    if response.status_code >= 400:
        return (
            None,
            _sse_error(
                'runtime_run_rejected',
                details=response.text,
                status_code=response.status_code,
            ),
        )
    try:
        run = response.json()
    except ValueError:
        return None, _sse_error('runtime_run_bad_response', details=response.text)
    run_id = run.get('run_id') if isinstance(run, dict) else None
    if not run_id:
        return (
            None,
            _sse_error(
                'runtime_run_missing_run_id',
                details='upstream /v1/runs response missing run_id',
            ),
        )
    return str(run_id), None


# SSE 心跳：上游静默期每 _SSE_KEEPALIVE_INTERVAL 秒发一个 SSE 注释帧（`:` 开头、无 data 行），
# 让 nginx/中间代理看到数据流动，不在首帧前的静默期（沙箱冷启动 + provision + LLM 首 token）
# 触发 proxy_read_timeout(60s) 切断长连接。注释帧被 daemon parse_relay_sse_frame 安全忽略
# （data 为空 → (None, false)），不污染回帧。
_SSE_KEEPALIVE_INTERVAL = 15.0
_SSE_KEEPALIVE_FRAME = b': keepalive\n\n'


async def _with_keepalive(
    inner: AsyncIterator[bytes], interval: float = _SSE_KEEPALIVE_INTERVAL
) -> AsyncIterator[bytes]:
    """给上游 SSE 字节流套心跳：`interval` 秒内上游无新字节就 yield 一个 SSE 注释帧。

    上游真有数据/正常结束则原样透传；上游意外异常透传给消费侧重抛（不静默吞，保零 fake）。
    `inner` 自身已把控制面/数据面错误转成 SSE error 帧，故心跳层几乎只在首帧前空窗起作用。
    """
    queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for item in inner:
                await queue.put(item)
        except Exception as exc:
            await queue.put(exc)
        finally:
            # None 哨兵 = 上游结束（inner 只 yield bytes，永不 yield None）。
            await queue.put(None)

    task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield _SSE_KEEPALIVE_FRAME
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
            raise errors.ForbiddenError(msg='该分身运行在本地，应经本地 runtime 派发，不走云端派发代理')
        status = getattr(row, 'status', 'active') or 'active'
        if status not in _DISPATCHABLE_STATUS:
            raise errors.ForbiddenError(msg='该分身非活跃状态，不可派发')
        profile_id = (runtime_profile_id or '').strip()
        if not profile_id:
            raise errors.RequestError(msg='runtime_profile_id is required')
        return profile_id

    async def cloud_runtime_health(
        self,
        *,
        runtime_profile_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """探云端 runtime 健康 → {online, health, detail}（供 daemon 判云端分身可达性）。

        语义（对齐双形态设计 08/02 §81「可达性 Gate 不感知位置」+「关机后仍在线」）：
        - **控制面可达 = online**：云端 runtime 进程在跑、可服务该分身。`health='ok'`。
          网关是「首次派发时懒启动」的——冷网关**不**判降级（否则没发过消息的云端分身永远
          显示「暂时离线」），只在 detail 里如实标 gateway_idle/gateway_unprovisioned。
        - **控制面不可达 = offline**：`online=False, health='offline'`（零 fake，如实报离线，
          不伪造在线）。
        """
        try:
            await self.runtime_client.probe(trace_id=trace_id)
        except HermesRuntimeError as exc:
            return {
                'online': False,
                'health': 'offline',
                'detail': exc.error or 'runtime_unavailable',
            }
        # 控制面可达 → 在线。再查 per-agent 网关 running 仅作观测细节，不改 online/health 判定。
        detail = 'gateway_unknown'
        try:
            status = await self.runtime_client.get_gateway_status(runtime_profile_id, trace_id=trace_id)
            running = bool(status.get('running')) if isinstance(status, dict) else False
            detail = 'gateway_running' if running else 'gateway_idle'
        except HermesRuntimeError:
            # 控制面在、但该分身网关未起/未 provision（懒启动）→ 仍按需可服务，不降级。
            detail = 'gateway_unprovisioned'
        return {'online': True, 'health': 'ok', 'detail': detail}

    async def relay_run_stream(
        self,
        *,
        runtime_profile_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """云端 → daemon 的 SSE 中继（套心跳保活）。

        实际中继逻辑在 `_relay_run_stream_inner`；外层用 `_with_keepalive` 包一层，首帧前的
        静默期（沙箱冷启动 + provision + LLM 首 token，易 >60s）周期性发 SSE 注释帧，避免
        nginx proxy_read_timeout 在 daemon↔本服务这段切断长连接。本生成器在路由返回
        StreamingResponse 后执行——**不得**触碰请求级 db 会话。
        """
        inner = self._relay_run_stream_inner(runtime_profile_id=runtime_profile_id, payload=payload, trace_id=trace_id)
        try:
            async for chunk in _with_keepalive(inner):
                yield chunk
        except Exception as exc:
            # 兜底：任何从 inner / 心跳层逃逸的未预期异常（上游返回意外结构、int(port) 之类
            # 的类型/解析错误等，不属于已捕获的 HermesRuntimeError / httpx.HTTPError）都转成
            # SSE error 帧 + 记完整 traceback，绝不让异常逃逸到 StreamingResponse——否则连接
            # 非正常关闭，daemon 侧只能看到无意义的 error decoding response body、拿不到真正的
            # 错误原因（零 fake：如实把内部错误下行，不伪造成功）。
            log.exception(f'cloud relay unexpected error for {runtime_profile_id}: {exc}')
            yield _sse_error('runtime_relay_internal_error', details=str(exc))

    async def _relay_run_stream_inner(
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
                if run_body.get('tool_execution') == 'disabled':
                    capability_error = await _negotiate_read_only_run_contract(
                        client,
                        base_url=base_url,
                        headers=headers,
                    )
                    if capability_error is not None:
                        yield capability_error
                        return

                # 数据面 1：POST /v1/runs 启动 run（短超时，应快速返回 run_id）。
                run_id, run_error = await _create_runtime_run(
                    client,
                    url=f'{base_url}{runs_create_path}',
                    body=run_body,
                    headers=headers,
                )
                if run_error is not None:
                    yield run_error
                    return
                assert run_id is not None

                # 数据面 2：SSE GET events → 逐帧中继（read 无超时，由上游终态收口）。
                # ⚠️ replace 的第一参必须是**字面占位符** '{run_id}'，不是 f'{run_id}'——后者会被
                # 插值成 run_id 的实际值，模板里没有该串 → 占位符原样残留 → GET 字面 /v1/runs/{run_id}
                # /events → 上游 404（曾让所有云端分身对话报 runtime_events_rejected/decoding error）。
                events_path = events_path_template.replace('{run_id}', str(run_id))  # noqa: RUF027
                async with client.stream('GET', f'{base_url}{events_path}', headers=headers) as events:
                    if events.status_code >= 400:
                        body = await events.aread()
                        yield _sse_error(
                            'runtime_events_rejected',
                            details=body.decode('utf-8', 'replace'),
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
        # 同上：字面占位符 '{run_id}'，不是 f'{run_id}'（endpoint 返回带占位符模板时才会触发 bug）。
        cancel_path = cancel_template.replace('{run_id}', str(run_id))  # noqa: RUF027
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

    async def list_skills(
        self,
        *,
        runtime_profile_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """列出云端分身运行时技能（设计 04：技能读能力在 RuntimeAdapter 内按 location 分叉）。

        纯读控制面：读 profile skills/ 目录，不启网关、不 provision。profile 未 provision 时
        sidecar 抛 HermesRuntimeError → 由本面如实上抛，daemon 侧对不可达优雅降级为空列表
        （对齐本地 runtime 未就绪时的读语义，不红 toast）。
        """
        data = await self.runtime_client.list_skills(runtime_profile_id, trace_id=trace_id)
        skills = data.get('skills') if isinstance(data, dict) else None
        return {'skills': skills if isinstance(skills, list) else []}

    async def read_skill(
        self,
        *,
        runtime_profile_id: str,
        skill_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """读取云端分身某技能正文（SKILL.md）。返回 sidecar 原形态（含 content）。"""
        return await self.runtime_client.read_skill(runtime_profile_id, skill_id, trace_id=trace_id)

    async def enable_skill(
        self,
        *,
        runtime_profile_id: str,
        skill_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """启用云端分身某技能。sidecar 成功返回 `{enabled:True}` → success=enabled；失败抛错上抛。"""
        data = await self.runtime_client.enable_skill(runtime_profile_id, skill_id, trace_id=trace_id)
        enabled = bool(data.get('enabled')) if isinstance(data, dict) else False
        return {'skill_id': skill_id, 'enabled': enabled, 'success': enabled}

    async def disable_skill(
        self,
        *,
        runtime_profile_id: str,
        skill_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """停用云端分身某技能。sidecar 成功返回 `{enabled:False}` → success=(not enabled)；失败抛错上抛。"""
        data = await self.runtime_client.disable_skill(runtime_profile_id, skill_id, trace_id=trace_id)
        enabled = bool(data.get('enabled')) if isinstance(data, dict) else True
        return {'skill_id': skill_id, 'enabled': enabled, 'success': not enabled}


hasn_agent_runtime_dispatch_service = HasnAgentRuntimeDispatchService()
