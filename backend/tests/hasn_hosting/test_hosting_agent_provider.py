"""hosting-agent provider 的诚实失败语义（H4 · 契约 §4.5⑥⑦）——零 mock。

不起假 agent：一半用例把服务判成「未配置」（prod 且无 URL），另一半真连一个必定关闭的本机端口，
断言 provider 归一成 `service_unconfigured` / `upstream_error`，而不是编一个「容器已就绪」。
"""

from __future__ import annotations

import socket

import pytest

from backend.app.hasn_hosting.constants import HOSTING_SERVICE_NAME
from backend.app.hasn_hosting.provider.agent_client import HostingAgentError, hosting_agent_provider
from backend.common.service_registry import get_service_spec, service_endpoint
from backend.common.services_config import derive_service_token, master_secret
from backend.core.conf import settings

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    """借一个立刻归还的端口号——保证该端口上没有监听者（连接必定被拒）。"""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


# ── 服务目录登记（跨仓口径） ──


def test_hosting_service_is_registered_with_contract_defaults() -> None:
    """契约 §1：服务注册名 hosting、默认端口 8003、健康路径 /health、派生令牌。"""
    spec = get_service_spec(HOSTING_SERVICE_NAME)
    assert spec.default_port == 8003
    assert spec.health_path == '/health'
    assert spec.derive_token is True
    assert spec.url_attr == 'HOSTING_AGENT_URL'


def test_hosting_token_matches_agent_side_derivation() -> None:
    """双向共用 derive_service_token(master, 'hosting')；两端无需各配（契约 §4.5⑦）。"""
    secret = master_secret()
    if not secret and not settings.HOSTING_AGENT_TOKEN:
        pytest.skip('本机未配 HUANXING_INTERNAL_SERVICE_SECRET / services.toml master_secret')
    expected = settings.HOSTING_AGENT_TOKEN or derive_service_token(secret, HOSTING_SERVICE_NAME)
    assert service_endpoint(HOSTING_SERVICE_NAME).token == expected


# ── 未配置：诚实信封，绝不静默连本机 ──


async def test_health_reports_service_unconfigured_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')
    monkeypatch.delenv('HOSTING_AGENT_URL', raising=False)
    monkeypatch.setattr(settings, 'HOSTING_AGENT_URL', '')

    health = await hosting_agent_provider.health()
    assert health == {'ok': False, 'error': 'service_unconfigured', 'message': '托管宿主服务未配置'}


async def test_create_node_raises_service_unconfigured_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')
    monkeypatch.delenv('HOSTING_AGENT_URL', raising=False)
    monkeypatch.setattr(settings, 'HOSTING_AGENT_URL', '')

    with pytest.raises(HostingAgentError) as exc:
        await hosting_agent_provider.create_node(
            node_id='n_cloud_unconfigured',
            owner_hasn_id='h_x',
            authorization_code='irrelevant',
            image_ref='registry.example.com/hasn-node:0.0.1',
            image_digest='sha256:' + '2' * 64,
            memory_mb=2048,
            cpus=1.0,
            backend_http_base='http://127.0.0.1:8020',
            backend_ws_url='ws://127.0.0.1:8020/api/v1/hasn/ws/node',
        )
    assert exc.value.code == 'service_unconfigured'
    assert exc.value.failure_reason is None


# ── 配置了但连不通：upstream_error，不吞不伪装 ──


async def test_health_reports_upstream_error_when_agent_unreachable(monkeypatch) -> None:
    monkeypatch.setenv('HOSTING_AGENT_URL', f'http://127.0.0.1:{_free_port()}')
    health = await hosting_agent_provider.health()
    assert health['ok'] is False
    assert health['error'] == 'upstream_error'


async def test_get_node_raises_upstream_error_when_agent_unreachable(monkeypatch) -> None:
    monkeypatch.setenv('HOSTING_AGENT_URL', f'http://127.0.0.1:{_free_port()}')
    with pytest.raises(HostingAgentError) as exc:
        await hosting_agent_provider.get_node('n_cloud_unreachable')
    assert exc.value.code == 'upstream_error'


async def test_try_get_node_returns_none_when_agent_unreachable(monkeypatch) -> None:
    """详情页柔性路径：宿主不通只让那几格留空，不把整页打成 500。"""
    monkeypatch.setenv('HOSTING_AGENT_URL', f'http://127.0.0.1:{_free_port()}')
    assert await hosting_agent_provider.try_get_node('n_cloud_unreachable') is None
