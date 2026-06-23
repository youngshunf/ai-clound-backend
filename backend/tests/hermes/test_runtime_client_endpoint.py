"""HermesRuntimeClient 连接三元组解析回归（M2 收编：__init__ 经统一服务目录）。

验证 ``HermesRuntimeClient.__init__`` 不再各读各的 settings，而是经
``service_registry.service_endpoint('hermes')`` 统一解析 base_url/token/timeout：

- dev 零配置 → 回落约定端口 127.0.0.1:8765，默认超时 10，token 不派生（derive_token=False）；
- env ``HUANXING_HERMES_RUNTIME_*`` 显式配置生效（去尾斜杠）；
- 构造参数（base_url/api_token/timeout_seconds）始终压过目录解析（逃生口）。

纯构造测试，无 HTTP（``_request`` 真正发网络的路径由其它集成测试覆盖）。
autouse 隔离磁盘 services.toml + 主密钥 env，任何机器确定可绿。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient
from backend.common import services_config
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_HERMES_ENV = (
    'HUANXING_HERMES_RUNTIME_BASE_URL',
    'HUANXING_HERMES_RUNTIME_API_TOKEN',
    'HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS',
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离磁盘 services.toml + 主密钥 + hermes env/settings，构造结果确定。"""
    monkeypatch.setenv('HUANXING_SERVICES_CONFIG', '/nonexistent/services.toml')
    monkeypatch.delenv('HUANXING_INTERNAL_SERVICE_SECRET', raising=False)
    services_config.reload_services_config()
    for key in _HERMES_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_BASE_URL', '', raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_API_TOKEN', '', raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS', 10, raising=False)
    yield
    services_config.reload_services_config()


def test_dev_unconfigured_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 未配 → 回落约定端口 8765、默认超时 10、即便有主密钥也不派生 token。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')

    client = HermesRuntimeClient()

    assert client.base_url == 'http://127.0.0.1:8765'
    assert client.timeout_seconds == pytest.approx(10.0)
    assert not client.api_token  # derive_token=False


def test_env_overrides_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """env HUANXING_HERMES_RUNTIME_* 显式配置生效（base_url 去尾斜杠）。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')
    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_BASE_URL', 'http://hermes.internal:9999/')
    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_API_TOKEN', 'svc-tok')
    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS', '25')

    client = HermesRuntimeClient()

    assert client.base_url == 'http://hermes.internal:9999'
    assert client.api_token == 'svc-tok'
    assert client.timeout_seconds == pytest.approx(25.0)


def test_prod_unconfigured_stays_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod 未配 → base_url 留空（_request 会归一 runtime_unavailable，绝不静默连本机）。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')

    client = HermesRuntimeClient()

    assert client.base_url == ''


def test_constructor_args_override_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式构造参数始终压过目录解析（逃生口 / 测试自指目标）。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')

    client = HermesRuntimeClient(
        base_url='http://override:7000/',
        api_token='arg-tok',
        timeout_seconds=3.5,
    )

    assert client.base_url == 'http://override:7000'
    assert client.api_token == 'arg-tok'
    assert client.timeout_seconds == pytest.approx(3.5)
