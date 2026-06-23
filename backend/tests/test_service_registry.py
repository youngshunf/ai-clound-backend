"""service_registry 单测：约定式解析（os.environ 优先 → settings → dev 本机默认 → prod 留空）。

纯函数测试，无 DB / 无 HTTP。覆盖零配置 dev、prod 漏配留空、显式覆盖、token/timeout 解析、目录完整性。
"""

from __future__ import annotations

import pytest

from backend.common.service_registry import (
    get_service_spec,
    iter_services,
    service_endpoint,
)
from backend.core.conf import settings


def _clear_finance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ('FINANCE_SERVICE_URL', 'FINANCE_SERVICE_TOKEN', 'FINANCE_SERVICE_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_URL', '')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TOKEN', '')
    monkeypatch.setattr(settings, 'FINANCE_SERVICE_TIMEOUT', 30)


def test_dev_unconfigured_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 环境未配 → 回落本机约定端口（零配置），configured=False。"""
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


def test_registry_catalog_complete() -> None:
    """目录登记了全部已知内部服务。"""
    names = {s.name for s in iter_services()}
    assert {'finance', 'quant', 'ragflow', 'hermes', 'newapi'} <= names
    # finance/quant 走连接池，其余为目录登记 only
    assert get_service_spec('finance').pooled is True
    assert get_service_spec('quant').pooled is True
    assert get_service_spec('ragflow').pooled is False


def test_unknown_service_raises() -> None:
    with pytest.raises(KeyError):
        get_service_spec('does-not-exist')
