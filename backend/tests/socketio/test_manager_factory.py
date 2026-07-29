"""传统 Socket.IO manager factory 测试。"""

from __future__ import annotations

import inspect

from pathlib import Path

import socketio

from backend.core.conf import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVER_FILE = PROJECT_ROOT / 'backend' / 'common' / 'socketio' / 'server.py'
ACTIONS_FILE = PROJECT_ROOT / 'backend' / 'common' / 'socketio' / 'actions.py'
REGISTRAR_FILE = PROJECT_ROOT / 'backend' / 'core' / 'registrar.py'
CELERY_FILE = PROJECT_ROOT / 'backend' / 'app' / 'task' / 'celery.py'


def _settings(**overrides: object) -> Settings:
    """基于真实 Settings 契约生成隔离配置。"""
    return Settings().model_copy(update=overrides)


def test_redis_factory_preserves_existing_manager_types_and_channel() -> None:
    """Redis 模式保持异步服务端与同步发布端的既有行为。"""
    from backend.common.socketio.manager import (
        SOCKETIO_CHANNEL,
        build_socketio_server_manager,
        build_socketio_sync_publisher,
    )

    config = _settings(
        SOCKETIO_MANAGER='redis',
        REDIS_HOST='127.0.0.1',
        REDIS_PORT=9397,
        REDIS_DATABASE=3,
        REDIS_PASSWORD='redis secret:/%',
    )
    server_manager = build_socketio_server_manager(config)
    publisher = build_socketio_sync_publisher(config)

    assert isinstance(server_manager, socketio.AsyncRedisManager)
    assert isinstance(publisher, socketio.RedisManager)
    assert server_manager.channel == SOCKETIO_CHANNEL == 'huanxing.socketio'
    assert publisher.channel == SOCKETIO_CHANNEL
    assert server_manager.redis_url == ('redis://:redis%20secret%3A%2F%25@127.0.0.1:9397/3')
    assert publisher.redis_url == server_manager.redis_url
    assert publisher.write_only is True


def test_rabbitmq_factory_returns_interoperable_async_and_sync_managers() -> None:
    """RabbitMQ 模式统一 exchange、凭据编码和 durable 声明。"""
    from backend.common.socketio.manager import (
        SOCKETIO_CHANNEL,
        HuanxingAsyncAioPikaManager,
        build_socketio_server_manager,
        build_socketio_sync_publisher,
    )

    config = _settings(
        SOCKETIO_MANAGER='rabbitmq',
        REALTIME_RABBITMQ_HOST='127.0.0.1',
        REALTIME_RABBITMQ_PORT=5672,
        REALTIME_RABBITMQ_VHOST='team/blue',
        REALTIME_RABBITMQ_USERNAME='service@huanxing',
        REALTIME_RABBITMQ_PASSWORD='secret:/%',
    )
    server_manager = build_socketio_server_manager(config)
    publisher = build_socketio_sync_publisher(config)

    assert isinstance(server_manager, HuanxingAsyncAioPikaManager)
    assert isinstance(server_manager, socketio.AsyncAioPikaManager)
    assert isinstance(publisher, socketio.KombuManager)
    assert server_manager.channel == publisher.channel == SOCKETIO_CHANNEL
    assert server_manager.queue_name.startswith('python-socketio.')
    assert server_manager.url == ('amqp://service%40huanxing:secret%3A%2F%25@127.0.0.1:5672/team%2Fblue')
    assert publisher.url == server_manager.url
    assert publisher.write_only is True
    assert publisher.exchange_options == {
        'type': 'fanout',
        'durable': True,
        'auto_delete': False,
    }


def test_call_sites_use_factory_and_have_startup_readiness_gates() -> None:
    """构造点必须收口，RabbitMQ 初始化失败不得静默回落 Redis。"""
    server = SERVER_FILE.read_text(encoding='utf-8')
    actions = ACTIONS_FILE.read_text(encoding='utf-8')
    registrar = REGISTRAR_FILE.read_text(encoding='utf-8')
    celery = CELERY_FILE.read_text(encoding='utf-8')

    assert 'build_socketio_server_manager' in server
    assert 'AsyncRedisManager(' not in server
    assert 'build_socketio_sync_publisher' in actions
    assert 'RedisManager(' not in actions
    assert 'assert_socketio_server_manager_ready' in registrar
    assert 'assert_socketio_sync_publisher_ready' in celery
    assert 'fallback' not in (server + actions).lower()


def test_rabbitmq_manager_declares_exclusive_auto_delete_queue() -> None:
    """每个 API worker 使用独立临时 queue，兼容 RabbitMQ 4.3 门禁。"""
    from backend.common.socketio.manager import (
        SOCKETIO_QUEUE_EXPIRES_MS,
        HuanxingAsyncAioPikaManager,
    )

    source = inspect.getsource(HuanxingAsyncAioPikaManager)

    assert SOCKETIO_QUEUE_EXPIRES_MS == 300_000
    assert 'exclusive=True' in source
    assert 'auto_delete=True' in source
    assert "'x-expires': SOCKETIO_QUEUE_EXPIRES_MS" in source
