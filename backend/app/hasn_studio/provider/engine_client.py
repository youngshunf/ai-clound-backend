"""montage_engine_provider —— 主云端 → montage-engine-service 的薄 HTTP client（设计 doc22 §3）。

**唯一耦合点**：StudioService（渲染提交/轮询/取片/原子工具/管线目录）经本 provider 说话引擎服务；
换部署/换引擎/双活只动这一层。**不 import OpenMontage/Remotion**（重依赖隔离在
huanxing-apps/montage-engine-service）。超时/不可达/非 JSON/非 2xx/引擎 ok:false 一律归一成诚实异常
（``StudioEngineError``，零 fake，绝不造假产物/绩效）。

配置来源：`MONTAGE_ENGINE_URL / MONTAGE_ENGINE_TOKEN / MONTAGE_ENGINE_TIMEOUT`——经
``service_endpoint('montage')`` 统一解析（进程环境变量优先 → settings → services.toml → dev 本机
127.0.0.1:8002 回落 → prod 留空归一 service_unconfigured，对齐 finance/quant）。本层是唯一取值入口、
契约不变。

引擎契约（huanxing-apps/montage-engine-service/service/app.py，内网 Bearer + Host 闸）。引擎返回**它自己**的
``{ok, service, interface, <payload>}`` 信封（业务失败仍 HTTP 200；坏请求/未知工具/未知 job 返 400/404）：
- GET  /v1/healthz                     探活（无鉴权）→ {ok, service, version, pipelines, tools, ...}
- GET  /v1/pipelines                   管线目录（只 production）→ ok 信封 {count, pipelines:[...]}
- GET  /v1/tools                       原子工具目录 → ok 信封 {count, tools:[...]}
- POST /v1/tools/{tool_name}           执行原子工具 body {inputs:{...}} → ok 信封 {result}
- POST /v1/render                      提交渲染 body {props?|demo?, pipeline_key?, composition_id?}
                                       → ok 信封 {job_id, status, ...}
- GET  /v1/render/{job_id}             渲染状态 → ok 信封 snapshot {job_id,status,progress,stage,error,...}
- GET  /v1/render/{job_id}/artifact    取成片 mp4 字节（FileResponse）

解包规约：HTTP 非 2xx → 抛 StudioEngineError；body ``ok==false`` → 抛 StudioEngineError（透传
error/message）；成功剥掉信封壳返回 payload（除 ok/service/interface 外的字段）。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.common.service_http import get_service_client
from backend.common.service_registry import ServiceEndpoint, service_endpoint

logger = logging.getLogger(__name__)

# 引擎信封的固定壳字段（成功时剥掉，只回业务 payload）。
_ENVELOPE_META = ('ok', 'service', 'interface')


class StudioEngineError(RuntimeError):
    """引擎传输层/业务失败（未配置/不可达/超时/非 JSON/非 2xx/引擎 ok:false）。

    StudioService 据此落 render_job.status=failed + 透传真实 error（零 fake）。
    """


def _endpoint() -> ServiceEndpoint:
    return service_endpoint('montage')


def _base() -> str:
    return _endpoint().base_url


def _timeout() -> float:
    return _endpoint().timeout


def _auth_headers() -> dict[str, str]:
    token = _endpoint().token
    return {'Authorization': f'Bearer {token}'} if token else {}


def _unwrap(data: Any, *, interface: str) -> dict[str, Any]:
    """剥引擎信封：ok==false 抛 StudioEngineError（透传 error/message）；成功回业务 payload（去壳字段）。"""
    if not isinstance(data, dict):
        raise StudioEngineError(f'引擎服务返回了非预期格式（{interface}）')
    if data.get('ok') is False:
        code = data.get('error') or 'engine_error'
        message = data.get('message') or ''
        raise StudioEngineError(f'引擎业务失败[{code}]: {message}')
    return {k: v for k, v in data.items() if k not in _ENVELOPE_META}


class StudioEngineProvider:
    """调 montage-engine-service（Bearer 内网令牌 + 超时 + 信封解包 + 错误归一）。"""

    async def list_pipelines(self) -> dict[str, Any]:
        """管线目录（只 production）。返回 {count, pipelines:[...]}。传输/业务失败抛 StudioEngineError。"""
        return await self._get('/v1/pipelines', interface='pipelines')

    async def list_tools(self) -> dict[str, Any]:
        """原子工具目录。返回 {count, tools:[...]}。"""
        return await self._get('/v1/tools', interface='tools')

    async def run_tool(self, tool_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """执行一个原子工具（创作段，provider 可能花钱）。返回 {result}。未知工具引擎返 404 → StudioEngineError。"""
        return await self._post(
            f'/v1/tools/{tool_name}', body={'inputs': dict(inputs or {})}, interface=f'tools.{tool_name}'
        )

    async def submit_render(
        self,
        *,
        props: dict[str, Any] | None = None,
        demo: str | None = None,
        pipeline_key: str | None = None,
        composition_id: str | None = None,
    ) -> dict[str, Any]:
        """提交一次渲染（job 式）。返回引擎渲染 snapshot（含 job_id/status/progress/stage/...）。

        ``demo`` 是引擎内置 demo-props 名（字符串，与 props 二选一）。缺 props/demo → 引擎返 400
        bad_request → StudioEngineError；传输层失败同样抛。
        """
        body: dict[str, Any] = {}
        if props is not None:
            body['props'] = props
        if demo is not None:
            body['demo'] = demo
        if pipeline_key is not None:
            body['pipeline_key'] = pipeline_key
        if composition_id is not None:
            body['composition_id'] = composition_id
        return await self._post('/v1/render', body=body, interface='render')

    async def get_render(self, job_id: str) -> dict[str, Any]:
        """轮询渲染 job snapshot。job 不存在引擎返 404 → StudioEngineError（含 'HTTP 404'）。"""
        return await self._get(f'/v1/render/{job_id}', interface='render.status')

    async def fetch_artifact(self, job_id: str) -> tuple[bytes, str]:
        """取成片字节（成功后物化用）。返回 (mp4_bytes, content_type)。失败/未完成抛 StudioEngineError。"""
        base = _base()
        if not base:
            raise StudioEngineError('视频引擎服务未配置（MONTAGE_ENGINE_URL 为空）')
        try:
            client = get_service_client('montage')
            resp = await client.get(
                f'{base}/v1/render/{job_id}/artifact', headers=_auth_headers(), timeout=_timeout()
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise StudioEngineError('引擎取片超时') from exc
        except httpx.HTTPStatusError as exc:
            raise StudioEngineError(
                f'引擎服务返回 HTTP {exc.response.status_code}: {_safe_detail(exc.response)}'
            ) from exc
        except httpx.HTTPError as exc:
            raise StudioEngineError(f'引擎服务不可达: {exc.__class__.__name__}') from exc
        content_type = resp.headers.get('content-type') or 'video/mp4'
        return resp.content, content_type

    async def healthz(self) -> dict[str, Any]:
        """探活引擎服务（owner 看板诊断用）；不抛，归一成 ok:false。"""
        base = _base()
        if not base:
            return {'ok': False, 'error': 'service_unconfigured', 'message': '视频引擎服务未配置'}
        try:
            client = get_service_client('montage')
            resp = await client.get(f'{base}/v1/healthz', timeout=5)
            resp.raise_for_status()
            body = resp.json()
            return {'ok': True, **(body if isinstance(body, dict) else {})}
        except (httpx.HTTPError, ValueError) as exc:
            return {'ok': False, 'error': 'upstream_error', 'message': exc.__class__.__name__}

    # ---------------------------------------------------------------- 内部 HTTP

    async def _get(self, path: str, *, interface: str) -> dict[str, Any]:
        base = _base()
        if not base:
            raise StudioEngineError('视频引擎服务未配置（MONTAGE_ENGINE_URL 为空）')
        try:
            client = get_service_client('montage')
            resp = await client.get(f'{base}{path}', headers=_auth_headers(), timeout=_timeout())
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise StudioEngineError(f'引擎服务请求超时（{interface}）') from exc
        except httpx.HTTPStatusError as exc:
            raise StudioEngineError(
                f'引擎服务返回 HTTP {exc.response.status_code}: {_safe_detail(exc.response)}'
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise StudioEngineError(f'引擎服务不可达: {exc.__class__.__name__}') from exc
        return _unwrap(data, interface=interface)

    async def _post(self, path: str, *, body: dict[str, Any], interface: str) -> dict[str, Any]:
        base = _base()
        if not base:
            raise StudioEngineError('视频引擎服务未配置（MONTAGE_ENGINE_URL 为空）')
        try:
            client = get_service_client('montage')
            resp = await client.post(
                f'{base}{path}', json=body, headers=_auth_headers(), timeout=_timeout()
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise StudioEngineError(f'引擎服务提交超时（{interface}）') from exc
        except httpx.HTTPStatusError as exc:
            # 引擎对坏请求/未知工具/未知 job 返非 2xx（400/404），且 body 仍是 {ok:false} 信封——
            # 透传状态码 + 信封里的 error/message，便于上层区分 404（job/工具不存在）。
            raise StudioEngineError(
                f'引擎服务返回 HTTP {exc.response.status_code}: {_safe_detail(exc.response)}'
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise StudioEngineError(f'引擎服务不可达: {exc.__class__.__name__}') from exc
        return _unwrap(data, interface=interface)


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            return str(body.get('message') or body.get('error') or body.get('detail') or body)
        return str(body)
    except ValueError:
        return (response.text or '')[:200]


montage_engine_provider = StudioEngineProvider()
