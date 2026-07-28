"""获客测试的真实 PII 加密/HMAC 配置。"""

from collections.abc import Iterator

import pytest

from backend.app.hasn_growth.service.pii_keyring import get_growth_pii_keyring
from backend.core.conf import settings


@pytest.fixture(scope='session', autouse=True)
def configure_growth_pii_test_keys() -> Iterator[None]:
    """测试进程使用固定测试密钥执行真实 AES-GCM/HMAC，不生成随机 fallback。"""
    previous = (
        settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
        settings.GROWTH_PII_HMAC_KEYS_JSON,
        settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
        settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
        settings.GROWTH_PII_NEW_WRITE_ENABLED,
    )
    settings.GROWTH_PII_ENCRYPTION_KEYS_JSON = (
        '{"1":"ERERERERERERERERERERERERERERERERERERERERERE=","2":"IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI="}'
    )
    settings.GROWTH_PII_HMAC_KEYS_JSON = (
        '{"1":"MzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzM=","2":"REREREREREREREREREREREREREREREREREREREREREQ="}'
    )
    settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION = 2
    settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION = 2
    settings.GROWTH_PII_NEW_WRITE_ENABLED = True
    get_growth_pii_keyring.cache_clear()
    try:
        yield
    finally:
        (
            settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
            settings.GROWTH_PII_HMAC_KEYS_JSON,
            settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
            settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
            settings.GROWTH_PII_NEW_WRITE_ENABLED,
        ) = previous
        get_growth_pii_keyring.cache_clear()
