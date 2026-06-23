"""quant_engine_provider —— 主云端 → quant-engine-service 的薄 HTTP client（设计 doc23 §5）。

**唯一耦合点**：QuantService（回测提交/轮询）经本 provider 说话引擎服务；换部署/加场所/双活只动这一层。
**不 import nautilus**（重依赖隔离在 huanxing-apps/quant-engine-service）。超时/不可达/非 JSON 一律归一成
诚实异常或 ok:false 信封（零 fake，绝不造假绩效）。

配置来源：`QUANT_ENGINE_URL / QUANT_ENGINE_TOKEN / QUANT_ENGINE_TIMEOUT`——**进程环境变量优先，回退 `settings`**
（`backend/core/conf.py`，对齐 FINANCE_SERVICE_*）。pydantic-settings 在导入期一次性读 env/.env 进 `settings`，
故 prod 配置经 .env / 进程 env 均生效；测试在运行时 `os.environ` 注入引擎地址（自启 loopback 引擎）也即时生效。
本层是唯一取值入口、契约不变。

引擎契约（quant-engine-service service/app.py）：
- POST   /v1/backtests        提交回测（job 式）→ {job_id, status, ...}
- GET    /v1/backtests/{id}   轮询状态/绩效     → {job_id, status, result:{metrics, equity_curve, error, ...}, ...}
- GET    /v1/healthz          探活（无鉴权）
鉴权：内网 Bearer 令牌（QUANT_ENGINE_TOKEN；空则引擎仅允许本机回环，开发态）。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.common.service_http import get_service_client
from backend.common.service_registry import service_endpoint

logger = logging.getLogger(__name__)


class QuantEngineError(RuntimeError):
    """引擎传输层失败（未配置/不可达/超时/非 JSON/非 2xx）——QuantService 据此落 run.status=failed + 透传真实 error。"""


def _engine_base() -> str:
    return service_endpoint('quant').base_url


def _engine_timeout() -> float:
    return service_endpoint('quant').timeout


def _auth_headers() -> dict[str, str]:
    token = service_endpoint('quant').token
    return {'Authorization': f'Bearer {token}'} if token else {}


class QuantEngineProvider:
    """调 quant-engine-service `/v1/backtests`（Bearer 内网令牌 + 超时 + 错误归一）。"""

    async def submit_backtest(self, request: dict[str, Any]) -> dict[str, Any]:
        """提交回测 job，返回引擎 BacktestJob dict（含 job_id/status）。传输层失败抛 QuantEngineError。"""
        base = _engine_base()
        if not base:
            raise QuantEngineError('量化引擎服务未配置（QUANT_ENGINE_URL 为空）')
        try:
            # 进程级单例连接池（keep-alive 复用 + HTTP/2 best-effort）；超时 per-request 传入。
            client = get_service_client('quant')
            resp = await client.post(
                f'{base}/v1/backtests', json=request, headers=_auth_headers(), timeout=_engine_timeout()
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise QuantEngineError('引擎服务提交超时') from exc
        except httpx.HTTPStatusError as exc:
            detail = _safe_detail(exc.response)
            raise QuantEngineError(f'引擎服务返回 HTTP {exc.response.status_code}: {detail}') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise QuantEngineError(f'引擎服务不可达: {exc.__class__.__name__}') from exc
        if not isinstance(data, dict) or 'job_id' not in data:
            raise QuantEngineError('引擎服务返回了非预期格式（缺 job_id）')
        return data

    async def get_backtest(self, job_id: str) -> dict[str, Any]:
        """轮询引擎回测 job 状态/绩效。传输层失败抛 QuantEngineError（含 404=job 不存在）。"""
        base = _engine_base()
        if not base:
            raise QuantEngineError('量化引擎服务未配置（QUANT_ENGINE_URL 为空）')
        try:
            client = get_service_client('quant')
            resp = await client.get(
                f'{base}/v1/backtests/{job_id}', headers=_auth_headers(), timeout=_engine_timeout()
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise QuantEngineError('引擎服务轮询超时') from exc
        except httpx.HTTPStatusError as exc:
            detail = _safe_detail(exc.response)
            raise QuantEngineError(f'引擎服务返回 HTTP {exc.response.status_code}: {detail}') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise QuantEngineError(f'引擎服务不可达: {exc.__class__.__name__}') from exc
        if not isinstance(data, dict):
            raise QuantEngineError('引擎服务返回了非预期格式')
        return data

    async def healthz(self) -> dict[str, Any]:
        """探活引擎服务（owner 看板诊断用）；不抛，归一成 ok:false。"""
        base = _engine_base()
        if not base:
            return {'ok': False, 'error': 'service_unconfigured', 'message': '量化引擎服务未配置'}
        try:
            client = get_service_client('quant')
            resp = await client.get(f'{base}/v1/healthz', timeout=5)
            resp.raise_for_status()
            body = resp.json()
            return {'ok': True, **(body if isinstance(body, dict) else {})}
        except (httpx.HTTPError, ValueError) as exc:
            return {'ok': False, 'error': 'upstream_error', 'message': exc.__class__.__name__}


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            return str(body.get('detail') or body)
        return str(body)
    except ValueError:
        return (response.text or '')[:200]


quant_engine_provider = QuantEngineProvider()
