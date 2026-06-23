"""seed_local_ragflow bootstrap 解析回归（M3：经统一服务目录，数据面凭据零改动）。

验证 seed 脚本读取的 **bootstrap/seed 级**配置（RSA 公钥 / 默认 embd/llm）经
``service_overrides('ragflow')`` 多源解析：显式 env RAGFLOW_* 优先 → settings → services.toml
``[service.ragflow]`` 扩展字段；URL 经 ``service_endpoint('ragflow')`` 解析含 dev 回落。

**不**覆盖 DB 写入（infra-gated：需真实 PG）；这里只锁定纯解析层，证明 per-instance 加密凭据
（``credential_ref``）不在 bootstrap 解析路径里——seed 只取公共 URL/公钥/默认模型。
autouse 隔离磁盘 services.toml + 主密钥 + RAGFLOW_* env/settings，任何机器确定可绿。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.common import services_config
from backend.common.service_registry import service_endpoint
from backend.common.services_config import service_overrides
from backend.core.conf import settings
from scripts.seed_local_ragflow import _bootstrap_value

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_RAGFLOW_ENV = (
    'RAGFLOW_PUBLIC_URL',
    'RAGFLOW_PUBLIC_RSA_PUBLIC_KEY',
    'RAGFLOW_DEFAULT_EMBD_ID',
    'RAGFLOW_DEFAULT_LLM_ID',
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离磁盘 services.toml + 主密钥 + RAGFLOW_* env/settings，解析确定。"""
    monkeypatch.setenv('HUANXING_SERVICES_CONFIG', '/nonexistent/services.toml')
    monkeypatch.delenv('HUANXING_INTERNAL_SERVICE_SECRET', raising=False)
    services_config.reload_services_config()
    for key in _RAGFLOW_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_PUBLIC_URL', '', raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', '', raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_DEFAULT_EMBD_ID', '', raising=False)
    monkeypatch.setattr(settings, 'RAGFLOW_DEFAULT_LLM_ID', '', raising=False)
    yield
    services_config.reload_services_config()


def _point_services_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    config = tmp_path / 'services.toml'
    config.write_text(body, encoding='utf-8')
    monkeypatch.setenv('HUANXING_SERVICES_CONFIG', str(config))
    services_config.reload_services_config()


def test_bootstrap_empty_when_nothing_configured() -> None:
    """全未配 → bootstrap 字段空（seed 据此打印「未配公钥」提示，但不造假）。"""
    overrides = service_overrides('ragflow')
    assert _bootstrap_value('RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', 'rsa_public_key', overrides) == ''
    assert _bootstrap_value('RAGFLOW_DEFAULT_EMBD_ID', 'default_embd_id', overrides) == ''
    assert _bootstrap_value('RAGFLOW_DEFAULT_LLM_ID', 'default_llm_id', overrides) == ''


def test_bootstrap_from_services_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """无 env/settings → 回落 services.toml [service.ragflow] 扩展字段；PEM 多行原样保留。"""
    pem = '-----BEGIN PUBLIC KEY-----\nLINE1\nLINE2\n-----END PUBLIC KEY-----'
    _point_services_config(
        monkeypatch,
        tmp_path,
        'master_secret = "m"\n'
        '[service.ragflow]\n'
        'url = "http://ragflow.toml:18082"\n'
        f'rsa_public_key = """{pem}"""\n'
        'default_embd_id = "bge-m3@local"\n'
        'default_llm_id = "qwen@local"\n',
    )
    overrides = service_overrides('ragflow')

    assert service_endpoint('ragflow').base_url == 'http://ragflow.toml:18082'
    assert _bootstrap_value('RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', 'rsa_public_key', overrides) == pem  # 换行原样
    assert _bootstrap_value('RAGFLOW_DEFAULT_EMBD_ID', 'default_embd_id', overrides) == 'bge-m3@local'
    assert _bootstrap_value('RAGFLOW_DEFAULT_LLM_ID', 'default_llm_id', overrides) == 'qwen@local'


def test_env_overrides_services_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """显式 env RAGFLOW_* 始终压过 services.toml（运行时注入 / 逃生口）。"""
    _point_services_config(
        monkeypatch,
        tmp_path,
        '[service.ragflow]\n'
        'url = "http://ragflow.toml:18082"\n'
        'rsa_public_key = "toml-pem"\n'
        'default_embd_id = "toml-embd"\n',
    )
    monkeypatch.setenv('RAGFLOW_PUBLIC_URL', 'http://ragflow.env:18082')
    monkeypatch.setenv('RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', 'env-pem')
    monkeypatch.setenv('RAGFLOW_DEFAULT_EMBD_ID', 'env-embd')
    overrides = service_overrides('ragflow')

    assert service_endpoint('ragflow').base_url == 'http://ragflow.env:18082'  # env 压过 toml
    assert _bootstrap_value('RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', 'rsa_public_key', overrides) == 'env-pem'
    assert _bootstrap_value('RAGFLOW_DEFAULT_EMBD_ID', 'default_embd_id', overrides) == 'env-embd'
    # 未被 env 覆盖的字段仍回落 toml
    assert _bootstrap_value('RAGFLOW_DEFAULT_LLM_ID', 'default_llm_id', overrides) == ''


def test_settings_between_env_and_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """settings（.env 落地）介于 env 与 services.toml 之间：无显式 env 时 settings 压过 toml。"""
    _point_services_config(
        monkeypatch,
        tmp_path,
        '[service.ragflow]\ndefault_llm_id = "toml-llm"\n',
    )
    monkeypatch.setattr(settings, 'RAGFLOW_DEFAULT_LLM_ID', 'settings-llm', raising=False)
    overrides = service_overrides('ragflow')

    assert _bootstrap_value('RAGFLOW_DEFAULT_LLM_ID', 'default_llm_id', overrides) == 'settings-llm'
