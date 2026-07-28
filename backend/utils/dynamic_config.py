from collections.abc import Callable, Mapping, Sequence

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.conf import settings
from backend.database.db import async_engine
from backend.plugin.config.enums import ConfigType
from backend.plugin.config.service.config_service import config_service

_sys_config_table_exists: bool | None = None


async def check_sys_config_table_exists() -> bool:
    """检查 sys_config 表是否存在"""
    global _sys_config_table_exists
    if _sys_config_table_exists is None:
        async with async_engine.connect() as conn:
            _sys_config_table_exists = await conn.run_sync(lambda c: inspect(c).has_table('sys_config', schema=None))
    return _sys_config_table_exists


def _to_bool(value: str) -> bool:
    """将字符串转换为布尔值"""
    return value == 'true'


def _normalize_config_values(entries: Sequence[object | None]) -> dict[str, str]:
    """统一数据库 ORM 条目与缓存反序列化字典的配置形态。"""
    configs: dict[str, str] = {}
    for entry in entries:
        if entry is None:
            continue
        if isinstance(entry, Mapping):
            key = entry.get('key')
            value = entry.get('value')
        else:
            key = getattr(entry, 'key', None)
            value = getattr(entry, 'value', None)
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError('动态配置条目契约错误：key 和 value 必须为字符串')
        configs[key] = value
    return configs


async def _load_config(
    db: AsyncSession,
    config_type: ConfigType,
    mapping: dict[str, Callable[[str], object]],
    status_key: str,
) -> None:
    """
    根据配置类型加载配置

    :param db: 数据库会话
    :param config_type: 配置类型枚举
    :param mapping: 配置映射 {config_key: converter}
    :param status_key: 状态键
    :return:
    """
    if not await check_sys_config_table_exists():
        return

    dynamic_config = await config_service.get_all(db=db, type=config_type)
    if not dynamic_config:
        return

    configs = _normalize_config_values(dynamic_config)
    if configs.get(status_key, '1') == '0':
        return

    for config_key, converter in mapping.items():
        if config_key in configs:
            setattr(settings, config_key, converter(configs[config_key]))


async def load_user_security_config(db: AsyncSession) -> None:
    """
    获取用户安全配置

    :param db: 数据库会话
    :return:
    """
    mapping: dict[str, Callable[[str], object]] = {
        'USER_LOCK_THRESHOLD': int,
        'USER_LOCK_SECONDS': int,
        'USER_PASSWORD_EXPIRY_DAYS': int,
        'USER_PASSWORD_REMINDER_DAYS': int,
        'USER_PASSWORD_HISTORY_CHECK_COUNT': int,
        'USER_PASSWORD_MIN_LENGTH': int,
        'USER_PASSWORD_MAX_LENGTH': int,
        'USER_PASSWORD_REQUIRE_SPECIAL_CHAR': _to_bool,
    }
    await _load_config(db, ConfigType.user_security, mapping, 'USER_SECURITY_CONFIG_STATUS')


async def load_login_config(db: AsyncSession) -> None:
    """
    获取登录配置

    :param db: 数据库会话
    :return:
    """
    mapping: dict[str, Callable[[str], object]] = {
        'LOGIN_CAPTCHA_ENABLED': _to_bool,
    }
    await _load_config(db, ConfigType.login, mapping, 'LOGIN_CONFIG_STATUS')


async def load_email_config(db: AsyncSession) -> None:
    """
    获取邮箱配置

    :param db: 数据库会话
    :return:
    """
    mapping: dict[str, Callable[[str], object]] = {
        'EMAIL_HOST': str,
        'EMAIL_PORT': int,
        'EMAIL_SSL': _to_bool,
        'EMAIL_USERNAME': str,
        'EMAIL_PASSWORD': str,
    }
    await _load_config(db, ConfigType.email, mapping, 'EMAIL_CONFIG_STATUS')
