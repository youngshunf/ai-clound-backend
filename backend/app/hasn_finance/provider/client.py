"""finance_provider —— 主云端 → finance-data-service 的薄 HTTP client（设计 §5）。

**唯一耦合点**：agent handler 与 owner read-API 共用本 provider 说话；换库/换源/双源兜底只动这一层。
**不 import akshare**（重依赖隔离在独立服务）。超时/不可达/错误一律归一成诚实 `ok:false` 信封（零 fake）。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.common.service_http import get_service_client
from backend.core.conf import settings

logger = logging.getLogger(__name__)


def _error(error: str, message: str, interface: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {'ok': False, 'error': error, 'message': message}
    if interface is not None:
        payload['interface'] = interface
    return payload


class FinanceProvider:
    """调 finance-data-service `/v1/query` 取数（Bearer svc-token + 超时 + 错误归一）。"""

    async def query(self, interface: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """取数：返回数据服务的规范化信封 dict（ok/error/source/rows...）。

        数据服务自带错误归一（业务失败也回 200 + ok:false）；本层只处理**传输层**失败
        （服务未配置/不可达/超时/非 JSON）→ 同样归一成诚实 ok:false，绝不抛、绝不造假。
        """
        base = (settings.FINANCE_SERVICE_URL or '').rstrip('/')
        if not base:
            return _error('service_unconfigured', '金融数据服务未配置（FINANCE_SERVICE_URL 为空）', interface)
        headers: dict[str, str] = {}
        if settings.FINANCE_SERVICE_TOKEN:
            headers['Authorization'] = f'Bearer {settings.FINANCE_SERVICE_TOKEN}'
        body = {'interface': interface, 'params': params or {}}
        try:
            # 进程级单例连接池（keep-alive 复用 + HTTP/2 best-effort）；超时 per-request 传入。
            client = get_service_client('finance')
            resp = await client.post(
                f'{base}/v1/query', json=body, headers=headers, timeout=settings.FINANCE_SERVICE_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            return _error('upstream_timeout', '数据源超时，请稍后重试', interface)
        except httpx.HTTPStatusError as exc:
            logger.warning('finance_provider HTTP %s for %s', exc.response.status_code, interface)
            return _error('upstream_error', f'数据源返回错误 HTTP {exc.response.status_code}', interface)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning('finance_provider unreachable for %s: %s', interface, exc.__class__.__name__)
            return _error('upstream_error', f'数据源暂时不可用: {exc.__class__.__name__}', interface)
        if not isinstance(data, dict):
            return _error('upstream_error', '数据源返回了非预期格式', interface)
        return data

    async def healthz(self) -> dict[str, Any]:
        """探活数据服务（owner 看板诊断用）。"""
        base = (settings.FINANCE_SERVICE_URL or '').rstrip('/')
        if not base:
            return {'ok': False, 'error': 'service_unconfigured', 'message': '金融数据服务未配置'}
        try:
            client = get_service_client('finance')
            resp = await client.get(f'{base}/v1/healthz', timeout=5)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {'ok': False, 'error': 'upstream_error', 'message': f'{exc.__class__.__name__}'}


finance_provider = FinanceProvider()
