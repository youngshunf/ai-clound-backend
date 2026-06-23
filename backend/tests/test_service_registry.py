"""service_registry 单测：约定式解析 + services.toml 覆盖 + 令牌集中派生。

纯函数测试，无 DB / 无 HTTP。覆盖零配置 dev、prod 漏配留空、显式覆盖、主密钥派生、目录完整性。
autouse fixture 隔离真实 services.toml（指向不存在路径 + 清缓存 + 清主密钥 env），任何环境可绿。
"""

from __future__ import annotations

import hashlib
import hmac

from typing import TYPE_CHECKING

import pytest

from backend.common import services_config
from backend.common.service_registry import (
    get_service_spec,
    iter_services,
    service_endpoint,
)
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _isolate_services_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离磁盘上的真实 services.toml 与主密钥 env，保证测试确定、任何机器可绿。"""
    monkeypatch.setenv('HUANXING_SERVICES_CONFIG', '/nonexistent/services.toml')
    monkeypatch.delenv('HUANXING_INTERNAL_SERVICE_SECRET', raising=False)
    services_config.reload_services_config()
    yield
    services_config.reload_services_config()


def _clear_finance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ('FINANCE_SERVICE_URL', 'FINANCE_SERVICE_TOKEN', 'FINANCE_SERVICE_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', '')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TOKEN', '')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TIMEOUT', 30)


def test_dev_unconfigured_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 环境未配 → 回落本机约定端口（零配置），configured=False，无主密钥则 token 空。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')

    ep = service_endpoint('finance')

    assert ep.base_url == 'http://127.0.0.1:8000'
    assert ep.configured is False
    assert not ep.token
    assert ep.timeout == pytest.approx(30.0)


def test_prod_unconfigured_stays_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod 环境未配 → base_url 留空（由 provider 归一 service_unconfigured，绝不静默连本机）。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')

    ep = service_endpoint('finance')

    assert not ep.base_url
    assert ep.configured is False


def test_settings_explicit_overrides_localhost_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 settings（.env）配置 → 用配置值，configured=True，即使在 dev 也不回落本机。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', 'http://finance.internal:9000/')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TOKEN', 'tok-abc')

    ep = service_endpoint('finance')

    assert ep.base_url == 'http://finance.internal:9000'  # 去尾斜杠
    assert ep.token == 'tok-abc'
    assert ep.configured is True


def test_os_environ_takes_priority_over_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """进程环境变量优先于 settings（支持运行时注入 / 测试自启 loopback 服务）。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', 'http://from-settings:1111')
    monkeypatch.setenv('FINANCE_SERVICE_URL', 'http://from-env:2222')
    monkeypatch.setenv('FINANCE_SERVICE_TOKEN', 'env-tok')

    ep = service_endpoint('finance')

    assert ep.base_url == 'http://from-env:2222'
    assert ep.token == 'env-tok'
    assert ep.configured is True


def test_timeout_resolution_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时：显式数值生效；非法/缺省回落 default_timeout。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TIMEOUT', 12)
    assert service_endpoint('finance').timeout == pytest.approx(12.0)

    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TIMEOUT', 0)  # 0/空 → 回落默认
    assert service_endpoint('finance').timeout == pytest.approx(30.0)


def test_token_derived_from_master_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """pooled 服务未显式配 token → 从单个 master_secret 派生 HMAC(master, 服务名)；各服务互不相同。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')

    def _expect(name: str) -> str:
        return hmac.new(b'master-xyz', name.encode(), hashlib.sha256).hexdigest()

    fin = service_endpoint('finance').token
    quant = service_endpoint('quant').token
    assert fin == _expect('finance')
    assert quant == _expect('quant')
    assert fin != quant  # 同一主密钥派生出的各服务 token 互异（按服务名）


def test_explicit_token_overrides_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 token（env/settings）始终压过主密钥派生（逃生口 / 向后兼容）。"""
    _clear_finance_env(monkeypatch)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TOKEN', 'explicit-tok')

    assert service_endpoint('finance').token == 'explicit-tok'


def test_non_pooled_service_not_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    """derive_token=False 服务（ragflow，有自有鉴权）即便有主密钥也不派生 token。"""
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')
    monkeypatch.delenv('RAGFLOW_PUBLIC_URL', raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_PUBLIC_URL', '', raising=False)

    assert not service_endpoint('ragflow').token


def test_newapi_pooled_but_not_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    """newapi 池化（pooled=True）但 derive_token=False：即便有主密钥也**绝不**派生 token。

    硬闸：守护「池化≠派生」的语义拆分——newapi 用外部 new-api 系统真实 admin 密钥，未显式配时
    必须留空（由调用方按未配处理），绝不能落入 master 派生分支被覆盖成派生值。
    """
    spec = get_service_spec('newapi')
    assert spec.pooled is True  # 走连接池
    assert spec.derive_token is False  # 但不派生

    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')
    monkeypatch.delenv('NEWAPI_ADMIN_ACCESS_TOKEN', raising=False)
    monkeypatch.setattr(settings, 'NEWAPI_ADMIN_ACCESS_TOKEN', '', raising=False)

    token = service_endpoint('newapi').token
    assert token == ''  # 未显式配 → 留空，**不**等于派生值
    assert token != hmac.new(b'master-xyz', b'newapi', hashlib.sha256).hexdigest()


def test_hermes_endpoint_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """hermes 经 service_endpoint 解析连接三元组：dev 回落 8765、默认超时 10、env 覆盖、token 不派生。"""
    for key in ('HUANXING_HERMES_RUNTIME_BASE_URL', 'HUANXING_HERMES_RUNTIME_API_TOKEN', 'HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_BASE_URL', '', raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_API_TOKEN', '', raising=False)
    monkeypatch.setattr(settings, 'HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS', 10, raising=False)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')  # 即便有主密钥也不派生

    ep = service_endpoint('hermes')
    assert ep.base_url == 'http://127.0.0.1:8765'  # dev 零配置回落约定端口
    assert ep.timeout == pytest.approx(10.0)  # default_timeout=10（hermes 历史默认）
    assert not ep.token  # derive_token=False → 不派生

    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_BASE_URL', 'http://hermes.internal:9999/')
    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_API_TOKEN', 'svc-tok')
    monkeypatch.setenv('HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS', '25')
    ep2 = service_endpoint('hermes')
    assert ep2.base_url == 'http://hermes.internal:9999'  # env 覆盖 + 去尾斜杠
    assert ep2.token == 'svc-tok'
    assert ep2.timeout == pytest.approx(25.0)


def test_ragflow_endpoint_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """ragflow 经 service_endpoint 解析：dev 回落 18082、env 覆盖、token 不派生（第三方 RSA 凭据在 DB）。"""
    monkeypatch.delenv('RAGFLOW_PUBLIC_URL', raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_PUBLIC_URL', '', raising=False)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'dev')
    monkeypatch.setenv('HUANXING_INTERNAL_SERVICE_SECRET', 'master-xyz')  # 即便有主密钥也不派生

    ep = service_endpoint('ragflow')
    assert ep.base_url == 'http://127.0.0.1:18082'  # dev 零配置回落约定端口
    assert ep.configured is False
    assert not ep.token  # derive_token=False → 不派生（per-instance RSA 凭据加密存 hasn_app_instance）

    monkeypatch.setenv('RAGFLOW_PUBLIC_URL', 'http://ragflow.internal:18082/')
    ep2 = service_endpoint('ragflow')
    assert ep2.base_url == 'http://ragflow.internal:18082'  # env 覆盖 + 去尾斜杠
    assert ep2.configured is True


def test_registry_catalog_complete() -> None:
    """目录登记了全部已知内部服务，且 pooled / derive_token 两维度取值符合 doc25 决策矩阵。"""
    names = {s.name for s in iter_services()}
    assert {'finance', 'quant', 'ragflow', 'hermes', 'newapi'} <= names
    # pooled：finance/quant/newapi 走连接池 + 健康复用池；ragflow/hermes 用临时 client
    assert get_service_spec('finance').pooled is True
    assert get_service_spec('quant').pooled is True
    assert get_service_spec('newapi').pooled is True
    assert get_service_spec('ragflow').pooled is False
    assert get_service_spec('hermes').pooled is False
    # derive_token：仅我方自研、两端受控的 finance/quant 派生；其余用真实/第三方鉴权，绝不派生
    assert get_service_spec('finance').derive_token is True
    assert get_service_spec('quant').derive_token is True
    assert get_service_spec('newapi').derive_token is False
    assert get_service_spec('ragflow').derive_token is False
    assert get_service_spec('hermes').derive_token is False


def test_unknown_service_raises() -> None:
    with pytest.raises(KeyError):
        get_service_spec('does-not-exist')
