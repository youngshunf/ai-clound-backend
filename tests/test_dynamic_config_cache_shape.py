import pytest

from backend.common.cache.decorator import _deserialize_result, _serialize_result
from backend.plugin.config.model import Config
from backend.utils.dynamic_config import _normalize_config_values


def test_normalize_config_values_accepts_database_and_cache_shapes() -> None:
    """动态配置同时接受数据库 ORM 条目和 Redis 反序列化字典。"""
    database_entry = Config(
        name='登录配置开关',
        type='login',
        key='LOGIN_CONFIG_STATUS',
        value='1',
    )
    cache_source = Config(
        name='登录验证码开关',
        type='login',
        key='LOGIN_CAPTCHA_ENABLED',
        value='true',
    )
    cache_entries = _deserialize_result(_serialize_result([cache_source]))

    assert _normalize_config_values([database_entry, *cache_entries, None]) == {
        'LOGIN_CONFIG_STATUS': '1',
        'LOGIN_CAPTCHA_ENABLED': 'true',
    }


def test_normalize_config_values_rejects_invalid_cache_entry() -> None:
    """缓存条目缺字段时显式失败，禁止静默回落或吞掉错误。"""
    with pytest.raises(TypeError, match='动态配置条目契约错误'):
        _normalize_config_values([{'key': 'LOGIN_CONFIG_STATUS'}])
