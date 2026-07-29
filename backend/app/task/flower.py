from __future__ import annotations

import argparse
import os
import re
import sys

from collections.abc import Mapping

from backend.core.conf import settings

FLOWER_BASIC_AUTH_ENV = 'FLOWER_BASIC_AUTH'
_FLOWER_USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9._-]+$')
_MINIMUM_PASSWORD_LENGTH = 24
_FLOWER_ADDRESSES = frozenset({'127.0.0.1', '0.0.0.0'})
_DEFAULT_CELERY_APPLICATION = f'{__package__}.celery:celery_app'


def validate_basic_auth(credential: str) -> str:
    """校验 Flower Basic Auth 凭据，异常中不得回显凭据。"""
    if ':' not in credential:
        raise ValueError('Flower Basic Auth 必须使用 username:password 格式')
    username, password = credential.split(':', maxsplit=1)
    if not username or _FLOWER_USERNAME_PATTERN.fullmatch(username) is None:
        raise ValueError('Flower Basic Auth 用户名格式不合法')
    if len(password) < _MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f'Flower Basic Auth 密码长度必须至少为 {_MINIMUM_PASSWORD_LENGTH} 位')
    if password == username:
        raise ValueError('Flower Basic Auth 密码不得与用户名相同')
    return credential


def build_flower_command(
    port: int,
    *,
    address: str = '127.0.0.1',
    url_prefix: str = '',
    celery_application: str = _DEFAULT_CELERY_APPLICATION,
) -> list[str]:
    """构造不含认证秘密的 Flower 命令。"""
    if not 1 <= port <= 65535:
        raise ValueError('Flower 端口必须在 1 到 65535 之间')
    if address not in _FLOWER_ADDRESSES:
        raise ValueError('Flower 只允许监听回环地址或容器全接口')
    if url_prefix and re.fullmatch(r'[A-Za-z0-9._-]+', url_prefix) is None:
        raise ValueError('Flower URL 前缀格式不合法')
    command = [
        sys.executable,
        '-m',
        'celery',
        '-A',
        celery_application,
        'flower',
        f'--address={address}',
        f'--port={port}',
    ]
    if url_prefix:
        command.append(f'--url-prefix={url_prefix}')
    return command


def build_flower_environment(
    credential: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """把已校验凭据放入子进程环境，避免出现在命令行和 Supervisor 配置。"""
    validated = validate_basic_auth(credential)
    environment = dict(os.environ if environ is None else environ)
    environment[FLOWER_BASIC_AUTH_ENV] = validated
    return environment


def run_flower(port: int, *, address: str, url_prefix: str) -> None:
    """用当前进程替换为 Flower，使 Supervisor 能直接管理实际服务进程。"""
    command = build_flower_command(port, address=address, url_prefix=url_prefix)
    environment = build_flower_environment(settings.FLOWER_BASIC_AUTH)
    os.execve(command[0], command, environment)


def main() -> None:
    """解析 Flower 启动参数。"""
    parser = argparse.ArgumentParser(description='安全启动 Flower 监控服务')
    parser.add_argument('--port', type=int, default=8555)
    parser.add_argument('--address', choices=sorted(_FLOWER_ADDRESSES), default='127.0.0.1')
    parser.add_argument('--url-prefix', default='')
    args = parser.parse_args()
    run_flower(args.port, address=args.address, url_prefix=args.url_prefix)


if __name__ == '__main__':
    main()
