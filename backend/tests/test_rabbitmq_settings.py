from __future__ import annotations

import importlib

import pytest

from pydantic import ValidationError

from backend.core.conf import Settings
from backend.core.path_conf import ENV_EXAMPLE_FILE_PATH

NEW_MESSAGE_INFRASTRUCTURE_SETTINGS = {
    'CELERY_BROKER_MODE',
    'FLOWER_BASIC_AUTH',
    'SOCKETIO_MANAGER',
    'REALTIME_RABBITMQ_HOST',
    'REALTIME_RABBITMQ_PORT',
    'REALTIME_RABBITMQ_VHOST',
    'REALTIME_RABBITMQ_USERNAME',
    'REALTIME_RABBITMQ_PASSWORD',
    'HASN_REALTIME_BUS',
    'HASN_REALTIME_SHADOW_RABBITMQ',
    'HASN_OFFLINE_RECOVERY',
}


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    for name, value in overrides.items():
        if isinstance(value, bool):
            value = str(value).lower()
        monkeypatch.setenv(name, str(value))
    return Settings()


def test_message_infrastructure_defaults_preserve_redis_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(monkeypatch)

    assert configured.CELERY_BROKER == 'redis'
    assert not configured.FLOWER_BASIC_AUTH
    assert configured.SOCKETIO_MANAGER == 'redis'
    assert configured.HASN_REALTIME_BUS == 'redis'
    assert configured.HASN_REALTIME_SHADOW_RABBITMQ is False
    assert configured.HASN_OFFLINE_RECOVERY == 'redis'
    assert configured.REALTIME_RABBITMQ_HOST == '127.0.0.1'
    assert configured.REALTIME_RABBITMQ_PORT == 5672
    assert configured.REALTIME_RABBITMQ_VHOST == 'huanxing'


@pytest.mark.parametrize(('raw_value', 'expected'), [('2', 2), ('3', 3)])
def test_redis_protocol_environment_string_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: int,
) -> None:
    """`.env` 文本值必须归一化为 redis-py 接受的整数协议版本。"""
    configured = _settings(monkeypatch, REDIS_PROTOCOL=raw_value)

    assert expected == configured.REDIS_PROTOCOL


def test_new_settings_are_declared_in_model_and_example_environment() -> None:
    example = ENV_EXAMPLE_FILE_PATH.read_text(encoding='utf-8')

    assert Settings.model_fields.keys() >= NEW_MESSAGE_INFRASTRUCTURE_SETTINGS
    for name in NEW_MESSAGE_INFRASTRUCTURE_SETTINGS:
        assert f'{name}=' in example


@pytest.mark.parametrize(
    ('overrides', 'missing_name'),
    [
        (
            {
                'CELERY_BROKER': 'rabbitmq',
                'CELERY_RABBITMQ_USERNAME': '',
                'CELERY_RABBITMQ_PASSWORD': 'celery-secret-must-not-leak',
            },
            'CELERY_RABBITMQ_USERNAME',
        ),
        (
            {
                'SOCKETIO_MANAGER': 'rabbitmq',
                'REALTIME_RABBITMQ_USERNAME': '',
                'REALTIME_RABBITMQ_PASSWORD': 'realtime-secret-must-not-leak',
            },
            'REALTIME_RABBITMQ_USERNAME',
        ),
        (
            {
                'HASN_REALTIME_BUS': 'rabbitmq',
                'REALTIME_RABBITMQ_USERNAME': '',
                'REALTIME_RABBITMQ_PASSWORD': 'realtime-secret-must-not-leak',
            },
            'REALTIME_RABBITMQ_USERNAME',
        ),
        (
            {
                'HASN_REALTIME_SHADOW_RABBITMQ': True,
                'REALTIME_RABBITMQ_USERNAME': '',
                'REALTIME_RABBITMQ_PASSWORD': 'realtime-secret-must-not-leak',
            },
            'REALTIME_RABBITMQ_USERNAME',
        ),
    ],
)
def test_selecting_rabbitmq_requires_role_credentials_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    missing_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(monkeypatch, **overrides)

    message = str(exc_info.value)
    assert missing_name in message
    assert 'celery-secret-must-not-leak' not in message
    assert 'realtime-secret-must-not-leak' not in message


def test_rabbitmq_active_bus_rejects_shadow_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match='HASN_REALTIME_SHADOW_RABBITMQ'):
        _settings(
            monkeypatch,
            HASN_REALTIME_BUS='rabbitmq',
            HASN_REALTIME_SHADOW_RABBITMQ=True,
            REALTIME_RABBITMQ_USERNAME='huanxing_realtime',
            REALTIME_RABBITMQ_PASSWORD='valid-secret',
        )


def test_complete_rabbitmq_settings_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(
        monkeypatch,
        CELERY_BROKER='rabbitmq',
        CELERY_RABBITMQ_USERNAME='huanxing_celery',
        CELERY_RABBITMQ_PASSWORD='celery-secret',
        SOCKETIO_MANAGER='rabbitmq',
        HASN_REALTIME_BUS='rabbitmq',
        HASN_REALTIME_SHADOW_RABBITMQ=False,
        REALTIME_RABBITMQ_USERNAME='huanxing_realtime',
        REALTIME_RABBITMQ_PASSWORD='realtime-secret',
    )

    assert configured.CELERY_BROKER == 'rabbitmq'
    assert configured.SOCKETIO_MANAGER == 'rabbitmq'
    assert configured.HASN_REALTIME_BUS == 'rabbitmq'


def test_non_conflicting_celery_broker_mode_overrides_legacy_dotenv_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(
        monkeypatch,
        CELERY_BROKER='redis',
        CELERY_BROKER_MODE='rabbitmq',
        CELERY_RABBITMQ_USERNAME='huanxing_celery',
        CELERY_RABBITMQ_PASSWORD='celery-secret',
    )

    assert configured.CELERY_BROKER_MODE == 'rabbitmq'
    assert configured.CELERY_BROKER == 'rabbitmq'


@pytest.mark.parametrize(
    ('overrides', 'error_fragment'),
    [
        (
            {
                'CELERY_RABBITMQ_USERNAME': 'admin',
                'CELERY_RABBITMQ_PASSWORD': 'V7rP4mZ2nQ8sK6xD9cT5wL3j',
                'CELERY_RABBITMQ_VHOST': 'huanxing',
            },
            'huanxing_celery',
        ),
        (
            {
                'CELERY_RABBITMQ_USERNAME': 'huanxing_celery',
                'CELERY_RABBITMQ_PASSWORD': 'too-short-secret',
                'CELERY_RABBITMQ_VHOST': 'huanxing',
            },
            '24',
        ),
        (
            {
                'CELERY_RABBITMQ_USERNAME': 'huanxing_celery',
                'CELERY_RABBITMQ_PASSWORD': 'V7rP4mZ2nQ8sK6xD9cT5wL3j',
                'CELERY_RABBITMQ_VHOST': '',
            },
            'CELERY_RABBITMQ_VHOST',
        ),
    ],
)
def test_production_celery_rabbitmq_rejects_placeholder_or_wrong_role(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    error_fragment: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            monkeypatch,
            ENVIRONMENT='prod',
            CELERY_BROKER='rabbitmq',
            **overrides,
        )

    message = str(exc_info.value)
    assert error_fragment in message
    assert 'V7rP4mZ2nQ8sK6xD9cT5wL3j' not in message
    assert 'too-short-secret' not in message


@pytest.mark.parametrize(
    ('overrides', 'error_fragment'),
    [
        (
            {
                'REALTIME_RABBITMQ_USERNAME': 'admin',
                'REALTIME_RABBITMQ_PASSWORD': 'V7rP4mZ2nQ8sK6xD9cT5wL3j',
                'REALTIME_RABBITMQ_VHOST': 'huanxing',
            },
            'huanxing_realtime',
        ),
        (
            {
                'REALTIME_RABBITMQ_USERNAME': 'huanxing_realtime',
                'REALTIME_RABBITMQ_PASSWORD': 'too-short-secret',
                'REALTIME_RABBITMQ_VHOST': 'huanxing',
            },
            '24',
        ),
        (
            {
                'REALTIME_RABBITMQ_USERNAME': 'huanxing_realtime',
                'REALTIME_RABBITMQ_PASSWORD': 'V7rP4mZ2nQ8sK6xD9cT5wL3j',
                'REALTIME_RABBITMQ_VHOST': 'shared',
            },
            'REALTIME_RABBITMQ_VHOST',
        ),
    ],
)
def test_production_realtime_rabbitmq_rejects_wrong_role_or_weak_secret(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    error_fragment: str,
) -> None:
    """生产 realtime 连接只能使用固定最小权限角色和强凭据。"""
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            monkeypatch,
            ENVIRONMENT='prod',
            SOCKETIO_MANAGER='rabbitmq',
            **overrides,
        )

    message = str(exc_info.value)
    assert error_fragment in message
    assert 'V7rP4mZ2nQ8sK6xD9cT5wL3j' not in message
    assert 'too-short-secret' not in message


def test_amqp_dsn_encodes_credentials_and_vhost() -> None:
    try:
        rabbitmq = importlib.import_module('backend.common.messaging.rabbitmq')
    except ModuleNotFoundError:
        pytest.fail('缺少统一 RabbitMQ DSN 构造模块')

    dsn = rabbitmq.build_amqp_dsn(
        host='rabbit.internal',
        port=5672,
        username='service@huanxing',
        password='secret:/%',
        vhost='team/blue',
    )

    assert dsn == ('amqp://service%40huanxing:secret%3A%2F%25@rabbit.internal:5672/team%2Fblue')


def test_rabbitmq_endpoint_description_never_contains_credentials() -> None:
    try:
        rabbitmq = importlib.import_module('backend.common.messaging.rabbitmq')
    except ModuleNotFoundError:
        pytest.fail('缺少统一 RabbitMQ 端点描述模块')

    description = rabbitmq.describe_rabbitmq_endpoint(
        host='rabbit.internal',
        port=5672,
        vhost='team/blue',
    )

    assert description == 'host=rabbit.internal port=5672 vhost=team/blue'
    assert 'username' not in description
    assert 'password' not in description
