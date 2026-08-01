"""传统 Socket.IO 跨进程 manager 的统一构造与启动门禁。"""

from __future__ import annotations

import uuid

from typing import Any, Literal, Protocol
from urllib.parse import quote

import aio_pika
import socketio

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue
from kombu import Connection, Exchange

from backend.common.messaging.rabbitmq import build_amqp_dsn
from backend.core.conf import settings

SOCKETIO_CHANNEL = 'huanxing.socketio'
SOCKETIO_QUEUE_EXPIRES_MS = 300_000


class SocketIOSettings(Protocol):
    """构造 manager 所需的最小配置契约。"""

    SOCKETIO_MANAGER: Literal['rabbitmq', 'redis']
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str
    REDIS_DATABASE: int
    REALTIME_RABBITMQ_HOST: str
    REALTIME_RABBITMQ_PORT: int
    REALTIME_RABBITMQ_VHOST: str
    REALTIME_RABBITMQ_USERNAME: str
    REALTIME_RABBITMQ_PASSWORD: str


class HuanxingAsyncAioPikaManager(socketio.AsyncAioPikaManager):
    """适配唤星固定 exchange 与 RabbitMQ 4.3 临时队列门禁。"""

    def __init__(
        self,
        url: str,
        channel: str = SOCKETIO_CHANNEL,
        *,
        write_only: bool = False,
        logger: Any = None,
    ) -> None:
        super().__init__(
            url=url,
            channel=channel,
            write_only=write_only,
            logger=logger,
        )
        self.queue_name = f'python-socketio.{uuid.uuid4()}'

    async def _exchange(self, channel: AbstractChannel) -> AbstractExchange:
        return await channel.declare_exchange(
            self.channel,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
            auto_delete=False,
        )

    async def _queue(
        self,
        channel: AbstractChannel,
        exchange: AbstractExchange,
    ) -> AbstractQueue:
        queue = await channel.declare_queue(
            self.queue_name,
            durable=False,
            exclusive=True,
            auto_delete=True,
            arguments={'x-expires': SOCKETIO_QUEUE_EXPIRES_MS},
        )
        await queue.bind(exchange)
        return queue


def _redis_url(config: SocketIOSettings) -> str:
    password = quote(config.REDIS_PASSWORD, safe='')
    return f'redis://:{password}@{config.REDIS_HOST}:{config.REDIS_PORT}/{config.REDIS_DATABASE}'


def _rabbitmq_url(config: SocketIOSettings) -> str:
    return build_amqp_dsn(
        host=config.REALTIME_RABBITMQ_HOST,
        port=config.REALTIME_RABBITMQ_PORT,
        username=config.REALTIME_RABBITMQ_USERNAME,
        password=config.REALTIME_RABBITMQ_PASSWORD,
        vhost=config.REALTIME_RABBITMQ_VHOST,
    )


def build_socketio_server_manager(
    config: SocketIOSettings = settings,
) -> socketio.AsyncRedisManager | HuanxingAsyncAioPikaManager:
    """为异步 API 进程构造 Socket.IO manager。"""
    if config.SOCKETIO_MANAGER == 'redis':
        return socketio.AsyncRedisManager(
            _redis_url(config),
            channel=SOCKETIO_CHANNEL,
        )
    if config.SOCKETIO_MANAGER == 'rabbitmq':
        return HuanxingAsyncAioPikaManager(
            _rabbitmq_url(config),
            channel=SOCKETIO_CHANNEL,
        )
    raise ValueError(f'不支持的 SOCKETIO_MANAGER：{config.SOCKETIO_MANAGER}')


def build_socketio_sync_publisher(
    config: SocketIOSettings = settings,
) -> socketio.RedisManager | socketio.KombuManager:
    """为同步 Celery 生命周期钩子构造可互通发布端。"""
    if config.SOCKETIO_MANAGER == 'redis':
        return socketio.RedisManager(
            _redis_url(config),
            channel=SOCKETIO_CHANNEL,
            write_only=True,
        )
    if config.SOCKETIO_MANAGER == 'rabbitmq':
        return socketio.KombuManager(
            _rabbitmq_url(config),
            channel=SOCKETIO_CHANNEL,
            write_only=True,
            exchange_options={
                'type': 'fanout',
                'durable': True,
                'auto_delete': False,
            },
        )
    raise ValueError(f'不支持的 SOCKETIO_MANAGER：{config.SOCKETIO_MANAGER}')


async def assert_socketio_server_manager_ready(
    manager: object,
) -> None:
    """在 API 启动期验证 RabbitMQ exchange 和临时 queue 权限。"""
    if not isinstance(manager, HuanxingAsyncAioPikaManager):
        return

    connection = await aio_pika.connect_robust(manager.url, timeout=10)
    try:
        channel = await connection.channel()
        exchange = await manager._exchange(channel)
        queue = await manager._queue(channel, exchange)
        await queue.delete(if_unused=False, if_empty=False)
    finally:
        await connection.close()


def assert_socketio_sync_publisher_ready(
    config: SocketIOSettings = settings,
) -> None:
    """在 Celery 应用初始化期验证同步发布角色能够声明 exchange。"""
    if config.SOCKETIO_MANAGER != 'rabbitmq':
        return

    connection = Connection(
        _rabbitmq_url(config),
        connect_timeout=10,
    )
    try:
        connection.ensure_connection(max_retries=0)
        channel = connection.channel()
        exchange = Exchange(
            SOCKETIO_CHANNEL,
            type='fanout',
            durable=True,
            auto_delete=False,
        )
        exchange.bind(channel).declare()
    finally:
        connection.close()
