from __future__ import annotations

import pytest

from backend.app.task.flower import (
    FLOWER_BASIC_AUTH_ENV,
    build_flower_command,
    build_flower_environment,
    validate_basic_auth,
)

VALID_BASIC_AUTH = 'flower-admin:V7rP4mZ2nQ8sK6xD9cT5wL3j'


def test_flower_command_binds_loopback_without_credentials() -> None:
    command = build_flower_command(port=8556)

    assert 'backend.app.task.celery:celery_app' in command
    assert command[-2:] == ['--address=127.0.0.1', '--port=8556']
    assert all('basic-auth' not in argument for argument in command)
    assert VALID_BASIC_AUTH not in command


def test_flower_command_supports_container_network_and_url_prefix() -> None:
    command = build_flower_command(
        port=8555,
        address='0.0.0.0',
        url_prefix='flower',
        celery_application='app.task.celery:celery_app',
    )

    assert 'app.task.celery:celery_app' in command
    assert command[-3:] == [
        '--address=0.0.0.0',
        '--port=8555',
        '--url-prefix=flower',
    ]


def test_flower_environment_contains_validated_credential_only_in_environment() -> None:
    environment = build_flower_environment(
        VALID_BASIC_AUTH,
        environ={'PATH': '/usr/bin'},
    )

    assert environment['PATH'] == '/usr/bin'
    assert environment[FLOWER_BASIC_AUTH_ENV] == VALID_BASIC_AUTH


@pytest.mark.parametrize(
    'credential',
    [
        '',
        'missing-separator',
        ':password-without-username',
        'flower-admin:',
        'flower admin:V7rP4mZ2nQ8sK6xD9cT5wL3j',
        'flower-admin:too-short',
        'flower-admin:flower-admin',
    ],
)
def test_flower_rejects_invalid_basic_auth_without_leaking_value(
    credential: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_basic_auth(credential)

    assert credential not in str(exc_info.value) or not credential


def test_flower_password_may_contain_colon() -> None:
    credential = 'flower-admin:V7rP4mZ2nQ8sK6xD9cT5wL3j:extra'

    assert validate_basic_auth(credential) == credential
