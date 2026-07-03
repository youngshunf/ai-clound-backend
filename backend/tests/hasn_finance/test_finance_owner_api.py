"""FIN-S3 — finance owner 只读 API HTTP 契约测试（经 ASGITransport 走完整 HTTP，零 mock）。

覆盖云端 owner read-API（设计 §6）：
- **鉴权闸**：无 Owner JWT → 401（DependsJwtAuth 在到 handler 前拦截）。
- **统一信封**：有 JWT → 200 + `{code,msg,data}`（裸返回会炸 daemon 解析，故必须 ResponseModel）。
- **诚实错误归一**：FINANCE_SERVICE_URL 未配置 → data.ok=false + service_unconfigured（零 fake，不伪造行情/0）。
- **入参校验**：缺必填 query（如 symbol）→ 422。

真实数据两路（Agent + Owner 出真实 K 线）在 S6 全栈 E2E 覆盖（需 finance-data-service 运行 + 网络）。
本测试只验证云端**契约层**（鉴权 + 信封 + 错误归一），不依赖外部数据服务，任何环境可绿。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request

from backend.app.hasn_finance.api.router import app as finance_app_router
from backend.common.security.jwt import DependsJwtAuth
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(finance_app_router)
    return app


@pytest_asyncio.fixture
async def authed_client() -> AsyncIterator[httpx.AsyncClient]:
    """带 owner 鉴权（override DependsJwtAuth 注入身份）的 HTTP client。"""
    app = _build_app()

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=1, is_superuser=False)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    app.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://e2e')
    try:
        yield client
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def anon_client() -> AsyncIterator[httpx.AsyncClient]:
    """无鉴权 client（验证 401 闸）。"""
    app = _build_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://e2e')
    try:
        yield client
    finally:
        await client.aclose()


_BASE = f'{settings.FASTAPI_API_V1_PATH}/finance/app'


@pytest.mark.asyncio
async def test_owner_endpoint_requires_jwt(anon_client: httpx.AsyncClient) -> None:
    """无 Owner JWT → 401（鉴权闸在 handler 前拦截，不泄露行情）。"""
    resp = await anon_client.get(f'{_BASE}/healthz')
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_healthz_wraps_envelope_and_honest_when_unconfigured(
    authed_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """healthz：200 + 统一信封；服务未配置 → data.ok=false service_unconfigured（零 fake）。"""
    # 钉 prod：service_registry 在 dev 下会把空 URL 回落本机约定端口（零配置便利），
    # 而本测试要验证「未配置 → 诚实 service_unconfigured」的 prod 契约，故显式钉死环境。
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod', raising=False)
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', '', raising=False)
    resp = await authed_client.get(f'{_BASE}/healthz')
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200, body  # 统一信封 {code,msg,data}
    data = body['data']
    assert data['ok'] is False
    assert data['error'] == 'service_unconfigured'


@pytest.mark.asyncio
async def test_quote_history_envelope_honest_when_unconfigured(
    authed_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A股K线：服务未配置 → 200 + data.ok=false（绝不伪造行情/0），interface 透传。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod', raising=False)  # 见 healthz 用例说明
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', '', raising=False)
    resp = await authed_client.get(f'{_BASE}/stock/quote-history', params={'symbol': '600519'})
    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['ok'] is False
    assert data['error'] == 'service_unconfigured'
    assert data['interface'] == 'stock.quote_history'


@pytest.mark.asyncio
async def test_quote_history_requires_symbol(authed_client: httpx.AsyncClient) -> None:
    """缺必填 symbol → 422（入参校验）。"""
    resp = await authed_client.get(f'{_BASE}/stock/quote-history')
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_macro_indicator_defaults_cpi(
    authed_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宏观指标：indicator 有默认 cpi，无必填；未配置仍诚实 ok=false。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod', raising=False)  # 见 healthz 用例说明
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', '', raising=False)
    resp = await authed_client.get(f'{_BASE}/macro/indicator')
    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['ok'] is False
    assert data['interface'] == 'macro.indicator'
