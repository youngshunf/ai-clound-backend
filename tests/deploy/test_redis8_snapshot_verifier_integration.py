"""Redis 8 快照校验器的真实双实例集成测试。"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pytest

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = PROJECT_ROOT / 'deploy' / 'redis8' / 'verify_snapshot.py'


@dataclass(frozen=True)
class RedisProcess:
    """真实 Redis 8 测试进程。"""

    port: int
    password: str
    process: subprocess.Popen[str]
    log_file: TextIO

    @property
    def url(self) -> str:
        """返回不写入测试输出的认证连接。"""
        return f'redis://:{self.password}@127.0.0.1:{self.port}/0'


def _reserve_loopback_port() -> int:
    """向内核申请一个当前可用的回环端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _start_redis(
    redis_server_bin: str,
    work_dir: Path,
) -> RedisProcess:
    """启动无持久化的隔离 Redis 8 进程。"""
    port = _reserve_loopback_port()
    password = secrets.token_urlsafe(48)
    log_file = (work_dir / 'redis.log').open('w', encoding='utf-8')
    process = subprocess.Popen(
        [
            redis_server_bin,
            '--bind',
            '127.0.0.1',
            '--protected-mode',
            'yes',
            '--port',
            str(port),
            '--requirepass',
            password,
            '--save',
            '',
            '--appendonly',
            'no',
            '--dir',
            str(work_dir),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server = RedisProcess(
        port=port,
        password=password,
        process=process,
        log_file=log_file,
    )
    probe = Redis(
        host='127.0.0.1',
        port=port,
        password=password,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f'Redis 8 启动失败，日志见 {work_dir / "redis.log"}')
            try:
                if probe.ping():
                    return server
            except (OSError, RedisConnectionError):
                time.sleep(0.05)
        pytest.fail(f'Redis 8 未在期限内就绪，日志见 {work_dir / "redis.log"}')
    finally:
        probe.close()


@pytest.fixture(scope='module')
def redis8_pair(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[RedisProcess, RedisProcess]]:
    """提供两个真实 Redis 8.8 进程并负责回收。"""
    if os.getenv('REDIS8_E2E') != '1':
        pytest.skip('设置 REDIS8_E2E=1 后运行真实 Redis 8.8 集成测试')

    redis_server_bin = os.getenv('REDIS8_SERVER_BIN') or shutil.which('redis-server')
    if not redis_server_bin:
        pytest.fail('未找到 redis-server，无法运行真实 Redis 8.8 集成测试')
    version_result = subprocess.run(
        [redis_server_bin, '--version'],
        check=True,
        capture_output=True,
        text=True,
    )
    if 'v=8.8.' not in version_result.stdout:
        pytest.fail(f'真实集成测试要求 Redis 8.8，当前为：{version_result.stdout.strip()}')

    servers = (
        _start_redis(redis_server_bin, tmp_path_factory.mktemp('redis8-source')),
        _start_redis(redis_server_bin, tmp_path_factory.mktemp('redis8-target')),
    )
    try:
        yield servers
    finally:
        for server in servers:
            server.process.terminate()
            try:
                server.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.process.kill()
                server.process.wait(timeout=5)
            server.log_file.close()


def _populate_all_core_types(client: Redis) -> None:
    """向真实实例写入全部受支持核心类型。"""
    client.set('deployment:string', 'value', px=600_000)
    client.rpush('deployment:list', 'first', 'second')
    client.sadd('deployment:set', 'one', 'two')
    client.zadd('deployment:zset', {'low': 1.25, 'high': 9.5})
    client.hset('deployment:hash', mapping={'field': 'value'})
    client.xadd(
        'deployment:stream',
        {'event': 'created'},
        id='1730000000000-0',
    )


def _run_verifier(source: RedisProcess, target: RedisProcess) -> subprocess.CompletedProcess[str]:
    """运行生产只读校验器。"""
    env = os.environ.copy()
    env['SOURCE_REDIS_URL'] = source.url
    env['TARGET_REDIS_URL'] = target.url
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_snapshot_verifier_accepts_equal_data_and_rejects_content_drift(
    redis8_pair: tuple[RedisProcess, RedisProcess],
) -> None:
    """一致快照通过，内容漂移失败，且输出不泄露原始键名。"""
    source_server, target_server = redis8_pair
    source = Redis.from_url(source_server.url)
    target = Redis.from_url(target_server.url)
    try:
        _populate_all_core_types(source)
        _populate_all_core_types(target)

        matching = _run_verifier(source_server, target_server)
        assert matching.returncode == 0, matching.stderr
        assert '"error_count": 0' in matching.stdout

        target.set('deployment:string', 'changed', px=600_000)
        drifted = _run_verifier(source_server, target_server)
        assert drifted.returncode == 1
        assert '内容不一致' in drifted.stderr
        assert 'deployment:string' not in drifted.stderr
        assert source_server.password not in drifted.stdout + drifted.stderr
        assert target_server.password not in drifted.stdout + drifted.stderr
    finally:
        source.close()
        target.close()
