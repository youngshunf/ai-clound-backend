"""传统 Socket.IO manager 的真实 RabbitMQ 跨进程 E2E。"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid

from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import TextIO

import pytest
import socketio

from backend.common.socketio.manager import (
    assert_socketio_sync_publisher_ready,
    build_socketio_sync_publisher,
)
from backend.core.conf import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVER_MODULE = 'backend.tests.socketio.socketio_rabbitmq_e2e_server:app'
REQUIRED_ENV = (
    'REALTIME_RABBITMQ_HOST',
    'REALTIME_RABBITMQ_PORT',
    'REALTIME_RABBITMQ_VHOST',
    'REALTIME_RABBITMQ_USERNAME',
    'REALTIME_RABBITMQ_PASSWORD',
)


def _reserve_loopback_port() -> int:
    """向内核申请一个当前可用的回环端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _wait_for_listener(
    process: subprocess.Popen[str],
    port: int,
    log_path: Path,
) -> None:
    """等待真实 ASGI 进程开始监听。"""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f'Socket.IO E2E 服务启动失败，日志见 {log_path}')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f'Socket.IO E2E 服务未在期限内监听，日志见 {log_path}')


def _start_server(
    port: int,
    env: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen[str], TextIO]:
    """启动一个使用真实 RabbitMQ manager 的独立 API 进程。"""
    log_file = log_path.open('w', encoding='utf-8')
    process = subprocess.Popen(
        [
            sys.executable,
            '-m',
            'uvicorn',
            SERVER_MODULE,
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
            '--log-level',
            'warning',
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_for_listener(process, port, log_path)
    return process, log_file


def _stop_servers(servers: Iterator[tuple[subprocess.Popen[str], TextIO]]) -> None:
    """终止并回收隔离 API 进程。"""
    for process, log_file in servers:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()


def _record_notification(
    received: tuple[list[dict[str, str]], list[dict[str, str]]],
    delivered: tuple[asyncio.Event, asyncio.Event],
    receiver: int,
    payload: dict[str, str],
) -> None:
    """记录真实客户端收到的通知。"""
    received[receiver].append(payload)
    delivered[receiver].set()


@pytest.mark.asyncio
async def test_sync_publisher_reaches_clients_on_two_api_processes(
    tmp_path: Path,
) -> None:
    """同步 Celery 发布端经真实 RabbitMQ 到达两个 API 进程的真实客户端。"""
    if os.getenv('SOCKETIO_RABBITMQ_E2E') != '1':
        pytest.skip('设置 SOCKETIO_RABBITMQ_E2E=1 后运行真实 RabbitMQ E2E')
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.fail('真实 RabbitMQ E2E 缺少配置：' + ', '.join(missing))

    env = os.environ.copy()
    env['SOCKETIO_MANAGER'] = 'rabbitmq'
    ports = (_reserve_loopback_port(), _reserve_loopback_port())
    logs = (tmp_path / 'api-1.log', tmp_path / 'api-2.log')
    servers: list[tuple[subprocess.Popen[str], TextIO]] = []
    clients = (socketio.AsyncClient(), socketio.AsyncClient())
    received: tuple[list[dict[str, str]], list[dict[str, str]]] = ([], [])
    delivered = (asyncio.Event(), asyncio.Event())
    message = f'socketio-e2e-{uuid.uuid4()}'

    for index, client in enumerate(clients):
        client.on(
            'task_notification',
            partial(_record_notification, received, delivered, index),
        )

    try:
        for port, log_path in zip(ports, logs, strict=True):
            servers.append(_start_server(port, env, log_path))
        for port, client in zip(ports, clients, strict=True):
            await client.connect(
                f'http://127.0.0.1:{port}',
                socketio_path='socket.io',
                transports=['websocket'],
                wait_timeout=10,
            )

        await asyncio.sleep(1)
        config = Settings().model_copy(update={'SOCKETIO_MANAGER': 'rabbitmq'})
        await asyncio.to_thread(assert_socketio_sync_publisher_ready, config)
        publisher = build_socketio_sync_publisher(config)
        await asyncio.to_thread(
            publisher.emit,
            'task_notification',
            {'msg': message},
        )
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in delivered)),
            timeout=10,
        )

        assert received == ([{'msg': message}], [{'msg': message}])
        assert isinstance(publisher, socketio.KombuManager)
        publisher.publisher_connection.close()
    finally:
        await asyncio.gather(
            *(client.disconnect() for client in clients if client.connected),
            return_exceptions=True,
        )
        _stop_servers(iter(servers))
