"""service_health —— 内部独立服务的统一健康聚合（消费 service_registry 目录）。

**解决什么**：服务一多，「哪个内部服务挂了 / 没配」散落各处、要逐个 curl 才知道。本模块遍历
:func:`service_registry.iter_services` 目录，对每个服务做一次轻量探活，聚合成一页可读的状态列表，
供管理端「内部服务健康」页 / 状态 CLI 一眼看全部死活（up/down/未配置 + 延迟 + 版本）。

探活策略（按登记的 ``health_path`` 分两类，零业务副作用）：
- 有 ``health_path``（finance/quant）：GET 健康路径，2xx 且 body.ok != false 视为 up，顺带读 version。
- 无 ``health_path``（ragflow/newapi）：GET 基址连通性探测，**任何 HTTP 响应**（含 4xx/5xx）即视为
  reachable=up（自有鉴权不在本探测职责内）；仅连接失败/超时为 down。
- ``base_url`` 为空：status='unconfigured'，不发网络（prod 漏配在此一眼可见；dev 零配置回落本机不算未配）。
"""

from __future__ import annotations

import asyncio
import logging
import time

from dataclasses import dataclass
from typing import Literal

import httpx

from backend.common.service_http import get_service_client
from backend.common.service_registry import ServiceSpec, iter_services, service_endpoint

logger = logging.getLogger(__name__)

ServiceStatus = Literal['up', 'down', 'unconfigured']

_PROBE_TIMEOUT = 5.0  # 探活超时（秒）；健康页应快速返回，慢服务即视为 down


@dataclass(frozen=True)
class ServiceHealthReport:
    """单个内部服务的健康快照（管理页 / 状态 CLI 一行）。"""

    name: str
    title: str
    status: ServiceStatus
    configured: bool  # 是否显式配置（False=dev 本机回落 或 未配）
    base_url: str  # 解析后的基址（内部 admin 可见，无凭据；未配为 ''）
    latency_ms: int | None  # 探活往返耗时；未发网络时为 None
    version: str | None  # 健康响应里的版本（若有）
    detail: str  # 人读说明（ok / HTTP 4xx / 连接失败 / 未配置）


def _extract_version(body: object) -> str | None:
    if isinstance(body, dict):
        for key in ('version', 'app_version', 'build'):
            val = body.get(key)
            if val:
                return str(val)
    return None


async def check_service_health(spec: ServiceSpec) -> ServiceHealthReport:
    """对单个登记服务探活，归一成 :class:`ServiceHealthReport`（不抛，失败即 down/unconfigured）。"""
    endpoint = service_endpoint(spec.name)
    if not endpoint.base_url:
        return ServiceHealthReport(
            name=spec.name,
            title=spec.title,
            status='unconfigured',
            configured=False,
            base_url='',
            latency_ms=None,
            version=None,
            detail='未配置（prod 漏配；dev 起在约定端口即可零配置连通）',
        )

    headers: dict[str, str] = {}
    if endpoint.token:
        headers['Authorization'] = f'Bearer {endpoint.token}'
    probe_url = f'{endpoint.base_url}{spec.health_path}' if spec.health_path else endpoint.base_url

    # pooled 服务复用进程级连接池（探测真实连接）；其余用临时 client，不污染池。
    started = time.monotonic()
    try:
        if spec.pooled:
            resp = await get_service_client(spec.name).get(
                probe_url, headers=headers, timeout=_PROBE_TIMEOUT
            )
        else:
            async with httpx.AsyncClient(trust_env=False, timeout=_PROBE_TIMEOUT) as client:
                resp = await client.get(probe_url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ServiceHealthReport(
            name=spec.name,
            title=spec.title,
            status='down',
            configured=endpoint.configured,
            base_url=endpoint.base_url,
            latency_ms=latency_ms,
            version=None,
            detail=f'不可达: {exc.__class__.__name__}',
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if spec.health_path:
        # 有健康端点：2xx 且 body.ok != false 才算 up。
        body: object = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        ok = resp.is_success and not (isinstance(body, dict) and body.get('ok') is False)
        return ServiceHealthReport(
            name=spec.name,
            title=spec.title,
            status='up' if ok else 'down',
            configured=endpoint.configured,
            base_url=endpoint.base_url,
            latency_ms=latency_ms,
            version=_extract_version(body),
            detail='ok' if ok else f'健康检查失败 HTTP {resp.status_code}',
        )

    # 无健康端点：任何 HTTP 响应即视为可达（自有鉴权返回的 4xx/5xx 也算「服务在」）。
    return ServiceHealthReport(
        name=spec.name,
        title=spec.title,
        status='up',
        configured=endpoint.configured,
        base_url=endpoint.base_url,
        latency_ms=latency_ms,
        version=None,
        detail=f'可达 HTTP {resp.status_code}',
    )


async def check_all_services_health() -> list[ServiceHealthReport]:
    """并发探活目录中全部内部服务，返回保持登记顺序的健康快照列表。"""
    specs = iter_services()
    reports = await asyncio.gather(*(check_service_health(spec) for spec in specs))
    return list(reports)
