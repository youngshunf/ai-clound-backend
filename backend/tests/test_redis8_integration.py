"""Redis 8.8 真实服务兼容性集成测试。

仅在 ``REDIS8_E2E=1`` 时启动本机 ``redis-server``。测试不使用 fake Redis，
并要求服务端版本为 8.8.x。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from redis import Redis as SyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from backend.app.hasn_im.adapters.routing import (
    delivery_bus,
    redis_presence_store,
    redis_realtime_wakeup_bus,
)
from backend.database.redis import RedisCli

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = PROJECT_ROOT / 'deploy' / 'redis8' / 'bootstrap.sh'


@dataclass(frozen=True)
class Redis8Server:
    """隔离 Redis 8.8 进程的连接信息。"""

    host: str
    port: int
    password: str


def test_bootstrap_uses_explicit_rdb_checker_entrypoint() -> None:
    """固定镜像必须显式进入 RDB 校验器，不能被默认入口改写为 Redis Server。"""
    source = BOOTSTRAP_SCRIPT.read_text(encoding='utf-8')

    assert '--entrypoint redis-check-rdb' in source


def test_bootstrap_loads_rdb_before_enabling_aof() -> None:
    """首次导入必须先载入 RDB，再生成并重启校验 AOF。"""
    source = BOOTSTRAP_SCRIPT.read_text(encoding='utf-8')

    disable_aof = source.index("sed 's/^appendonly yes$/appendonly no/'")
    validate_loaded_keys = source.index(
        '[ "$loaded_keys" -gt 0 ] || fail \'RDB 未载入任何有效键，拒绝创建空 AOF\'',
    )
    enable_aof = source.index('CONFIG SET appendonly yes')
    restart = source.index('docker compose restart redis8')

    assert disable_aof < validate_loaded_keys < enable_aof < restart


def _reserve_loopback_port() -> int:
    """向内核申请一个当前可用的本机端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope='module')
def redis8_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Redis8Server]:
    """启动只监听回环地址、无持久化的真实 Redis 8.8。"""
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

    work_dir = tmp_path_factory.mktemp('redis8')
    log_path = work_dir / 'redis.log'
    port = _reserve_loopback_port()
    password = secrets.token_urlsafe(48)
    log_file = log_path.open('w', encoding='utf-8')
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
    server = Redis8Server(host='127.0.0.1', port=port, password=password)
    probe = SyncRedis(
        host=server.host,
        port=server.port,
        password=server.password,
        socket_connect_timeout=1,
        socket_timeout=1,
    )

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f'Redis 8.8 启动失败，日志见 {log_path}')
            try:
                if probe.ping():
                    break
            except (OSError, RedisConnectionError):
                time.sleep(0.05)
        else:
            pytest.fail(f'Redis 8.8 未在期限内就绪，日志见 {log_path}')
        yield server
    finally:
        probe.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()


@pytest_asyncio.fixture
async def redis8_client(redis8_server: Redis8Server) -> AsyncIterator[RedisCli]:
    """为每个用例提供 RESP2 客户端并清空隔离数据库。"""
    client = RedisCli(
        host=redis8_server.host,
        port=redis8_server.port,
        password=redis8_server.password,
        db=15,
        protocol=2,
    )
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', [2, 3])
async def test_resp_pipeline_and_ttl(
    redis8_server: Redis8Server,
    protocol: Literal[2, 3],
) -> None:
    """RESP2/RESP3 均保持既有响应形状，并支持 pipeline 与 TTL。"""
    client = RedisCli(
        host=redis8_server.host,
        port=redis8_server.port,
        password=redis8_server.password,
        db=14,
        protocol=protocol,
    )
    try:
        assert client.connection_pool.get_protocol() == protocol
        info = await client.info('server')
        assert str(info['redis_version']).startswith('8.8.')

        async with client.pipeline(transaction=True) as pipeline:
            pipeline.set('redis8:pipeline', 'value', ex=30)
            pipeline.get('redis8:pipeline')
            pipeline.ttl('redis8:pipeline')
            results = await pipeline.execute()

        assert results[0] is True
        assert results[1] == 'value'
        assert 0 < results[2] <= 30
    finally:
        await client.delete('redis8:pipeline')
        await client.aclose()


@pytest.mark.asyncio
async def test_pubsub_round_trip(
    redis8_server: Redis8Server,
) -> None:
    """真实 Pub/Sub 在 RESP3 客户端之间完成发布与订阅。"""
    subscriber_client = RedisCli(
        host=redis8_server.host,
        port=redis8_server.port,
        password=redis8_server.password,
        db=13,
        protocol=3,
    )
    publisher_client = RedisCli(
        host=redis8_server.host,
        port=redis8_server.port,
        password=redis8_server.password,
        db=13,
        protocol=3,
    )
    pubsub = subscriber_client.pubsub()
    channel = 'redis8:e2e:pubsub'
    try:
        await pubsub.subscribe(channel)
        subscription = await pubsub.get_message(
            ignore_subscribe_messages=False,
            timeout=2,
        )
        assert subscription is not None
        assert subscription['type'] == 'subscribe'

        assert await publisher_client.publish(channel, 'payload') == 1
        deadline = asyncio.get_running_loop().time() + 2
        message = None
        while asyncio.get_running_loop().time() < deadline:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=0.2,
            )
            if message is not None:
                break
        assert message is not None
        assert message['channel'] == channel
        assert message['data'] == 'payload'
    finally:
        await pubsub.aclose()
        await subscriber_client.aclose()
        await publisher_client.aclose()


@pytest.mark.asyncio
async def test_realtime_wakeup_adapter_round_trip(
    redis8_server: Redis8Server,
    redis8_client: RedisCli,
) -> None:
    """真实验证 wake-up adapter 的定向、广播和订阅资源回收。"""

    def create_subscriber() -> RedisCli:
        return RedisCli(
            host=redis8_server.host,
            port=redis8_server.port,
            password=redis8_server.password,
            db=15,
            protocol=2,
        )

    events: list[dict[str, object]] = []
    event_received = asyncio.Event()

    async def handle(event: dict[str, object]) -> None:  # noqa: RUF029
        events.append(event)
        event_received.set()

    bus = redis_realtime_wakeup_bus.RedisRealtimeWakeupBus(
        publisher=redis8_client,
        subscriber_factory=create_subscriber,
    )
    bus.start(handle)
    try:
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            result = await redis8_client.execute_command(
                'PUBSUB',
                'NUMSUB',
                redis_realtime_wakeup_bus.WS_DELIVERY_CHANNEL,
            )
            if int(result[1]) == 1:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail('Redis wake-up adapter 未在期限内完成订阅')

        await bus.publish_node_wakeup('node-real')
        await asyncio.wait_for(event_received.wait(), timeout=2)
        assert events == [{'node_id': 'node-real'}]

        event_received.clear()
        await bus.publish_broadcast('FRAME')
        await asyncio.wait_for(event_received.wait(), timeout=2)
        assert events[-1] == {'broadcast': True, 'payload': 'FRAME'}
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_presence_lua_lock_and_ttl(
    redis8_client: RedisCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实验证 presence Lua、代际门禁、分布式锁和 TTL。"""
    monkeypatch.setattr(redis_presence_store, 'redis_client', redis8_client)
    node_id = 'redis8-e2e-node'
    connection_id = 'redis8-e2e-connection'

    await redis_presence_store.set_node_presence(
        node_id,
        'desktop',
        2,
        connection_id,
        connected_at='2026-07-30T00:00:00+08:00',
        ttl_secs=30,
    )
    assert (
        await redis8_client.hget(
            redis_presence_store.NODE_GENERATION_KEY,
            node_id,
        )
        == connection_id
    )
    alive_key = redis_presence_store.node_alive_key(node_id)
    assert 0 < await redis8_client.ttl(alive_key) <= 30
    assert (
        await redis_presence_store.refresh_presence_if_current(
            node_id,
            '旧连接',
            ttl_secs=30,
        )
        is False
    )
    assert (
        await redis_presence_store.refresh_presence_if_current(
            node_id,
            connection_id,
            ttl_secs=30,
        )
        is True
    )

    first_lock = redis8_client.lock('redis8:e2e:lock', timeout=10)
    second_lock = redis8_client.lock('redis8:e2e:lock', timeout=10)
    assert await first_lock.acquire(blocking=False) is True
    assert await second_lock.acquire(blocking=False) is False
    await first_lock.release()
    assert await second_lock.acquire(blocking=False) is True
    await second_lock.release()

    assert (
        await redis_presence_store.unregister_node_if_current(
            node_id,
            connection_id,
        )
        is True
    )
    assert await redis8_client.exists(alive_key) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('move_mode', ['lua', 'lmove'])
async def test_pending_processing_fifo(
    redis8_client: RedisCli,
    monkeypatch: pytest.MonkeyPatch,
    move_mode: Literal['lua', 'lmove'],
) -> None:
    """Redis 6 Lua 兼容路径与 Redis 8 LMOVE 原生路径均保持 FIFO。"""
    monkeypatch.setattr(delivery_bus, 'redis_client', redis8_client)
    pending_key = f'redis8:e2e:pending:{move_mode}'
    processing_key = f'redis8:e2e:processing:{move_mode}'
    await redis8_client.rpush(pending_key, 'first', 'second', 'third')

    moved = [
        await delivery_bus._move_pending_to_processing(
            pending_key,
            processing_key,
            move_mode=move_mode,
        )
        for _ in range(3)
    ]

    assert moved == ['first', 'second', 'third']
    assert await redis8_client.lrange(pending_key, 0, -1) == []
    assert await redis8_client.lrange(processing_key, 0, -1) == [
        'first',
        'second',
        'third',
    ]


def test_native_opentelemetry_metrics_with_async_pool(
    redis8_server: Redis8Server,
    tmp_path: Path,
) -> None:
    """隔离解释器验证原生 OTel 指标不会触发异步连接池回调错误。"""
    script_path = tmp_path / 'verify_redis_otel.py'
    script_path.write_text(
        """
import asyncio
import sys

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from redis.observability import OTelConfig, get_observability_instance

from backend.database.redis import RedisCli


async def main() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    observability = get_observability_instance()
    observability.init(OTelConfig())
    client = RedisCli(
        host='127.0.0.1',
        port=int(sys.argv[1]),
        password=sys.argv[2],
        db=12,
        protocol=3,
    )
    try:
        await asyncio.gather(*(client.ping() for _ in range(8)))
        connection_counts = client.connection_pool.get_connection_count()
        assert sum(count for count, _attributes in connection_counts) >= 1
        metrics_data = reader.get_metrics_data()
        names = {
            metric.name
            for resource_metric in metrics_data.resource_metrics
            for scope_metric in resource_metric.scope_metrics
            for metric in scope_metric.metrics
        }
        assert 'db.client.connection.count' in names
    finally:
        await client.aclose()
        observability.shutdown()
        provider.shutdown()


asyncio.run(main())
""".strip(),
        encoding='utf-8',
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            str(redis8_server.port),
            redis8_server.password,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert 'Callback failed' not in result.stderr
