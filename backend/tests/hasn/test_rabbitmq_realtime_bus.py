"""HASN RabbitMQ realtime wake-up adapter 契约测试。"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import socket
import subprocess
import time
import uuid

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import pytest

from backend.app.hasn_im.adapters.routing.rabbitmq_realtime_wakeup_bus import (
    REALTIME_EXCHANGE,
    DecodedWakeupEvent,
    RabbitMQRealtimeWakeupBus,
    decode_wakeup_event,
    encode_broadcast_wakeup,
    encode_node_wakeup,
)
from backend.app.hasn_im.adapters.routing.realtime_wakeup_factory import (
    ShadowRealtimeWakeupBus,
    build_realtime_wakeup_bus,
    wait_realtime_wakeup_ready,
)
from backend.app.hasn_im.adapters.routing.redis_realtime_wakeup_bus import (
    RedisRealtimeWakeupBus,
)
from backend.app.hasn_im.ports.realtime_wakeup_bus import RealtimeWakeupBus
from backend.database.redis import redis_client


@dataclass
class RabbitMQTestSettings:
    """不含可用凭据的纯构造配置。"""

    REALTIME_RABBITMQ_HOST: str = '127.0.0.1'
    REALTIME_RABBITMQ_PORT: int = 5672
    REALTIME_RABBITMQ_VHOST: str = 'huanxing'
    REALTIME_RABBITMQ_USERNAME: str = 'huanxing_realtime'
    REALTIME_RABBITMQ_PASSWORD: str = '仅用于纯构造测试'
    HASN_REALTIME_BUS: Literal['rabbitmq', 'redis'] = 'rabbitmq'
    HASN_REALTIME_SHADOW_RABBITMQ: bool = False


def _wait_for_real_broker_listener(
    *,
    broker_host: str,
    broker_port: int,
) -> None:
    """等待宿主映射的真实 AMQP listener 完成启动。"""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(
                (broker_host, broker_port),
                timeout=0.5,
            ):
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError('隔离 RabbitMQ 重启后 AMQP listener 未在 60 秒内恢复')


def test_node_event_schema_round_trip() -> None:
    """定向事件包含可对账元数据，解码后保持既有 delivery 形状。"""
    body = encode_node_wakeup(
        'node-01',
        event_id='018f2f6e-7c00-7000-8000-000000000001',
        sent_at_ms=1_777_500_000_000,
    )

    raw = json.loads(body)
    assert raw == {
        'version': 1,
        'event_id': '018f2f6e-7c00-7000-8000-000000000001',
        'sent_at_ms': 1_777_500_000_000,
        'kind': 'node',
        'node_id': 'node-01',
    }
    decoded = decode_wakeup_event(body)
    assert decoded is not None
    assert decoded.event_id == raw['event_id']
    assert decoded.sent_at_ms == raw['sent_at_ms']
    assert decoded.delivery_event == {'node_id': 'node-01'}


def test_broadcast_event_schema_round_trip() -> None:
    """广播事件解码后继续使用既有 broadcast/payload 形状。"""
    body = encode_broadcast_wakeup(
        '{"method":"hasn.sync.invalidate"}',
        event_id='018f2f6e-7c00-7000-8000-000000000002',
        sent_at_ms=1_777_500_000_001,
    )

    decoded = decode_wakeup_event(body)
    assert decoded is not None
    assert decoded.delivery_event == {
        'broadcast': True,
        'payload': '{"method":"hasn.sync.invalidate"}',
    }


@pytest.mark.parametrize(
    'body',
    [
        b'not-json',
        b'[]',
        b'{}',
        b'{"version":2,"event_id":"x","sent_at_ms":1,"kind":"node","node_id":"n"}',
        b'{"version":1,"event_id":"","sent_at_ms":1,"kind":"node","node_id":"n"}',
        b'{"version":1,"event_id":"x","sent_at_ms":0,"kind":"node","node_id":"n"}',
        b'{"version":1,"event_id":"x","sent_at_ms":1,"kind":"node","node_id":""}',
        b'{"version":1,"event_id":"x","sent_at_ms":1,"kind":"broadcast"}',
        b'{"version":1,"event_id":"x","sent_at_ms":1,"kind":"unknown"}',
    ],
)
def test_invalid_event_is_rejected(body: bytes) -> None:
    """畸形或未知 schema 必须返回空，由消费者告警并 ACK。"""
    assert decode_wakeup_event(body) is None


def test_worker_queue_name_is_stable_and_permission_scoped() -> None:
    """同一 worker 实例重连时复用固定临时队列名。"""
    bus = RabbitMQRealtimeWakeupBus(
        config=RabbitMQTestSettings(),
        instance_id='api-worker-01',
    )

    assert isinstance(bus, RealtimeWakeupBus)
    assert REALTIME_EXCHANGE == 'huanxing.realtime'
    assert bus.instance_id == 'api-worker-01'
    assert bus.queue_name == 'huanxing.realtime.worker.api-worker-01'
    assert bus.queue_name == bus.queue_name


@pytest.mark.parametrize('instance_id', ['', '../worker', 'worker with space', 'a' * 65])
def test_invalid_worker_instance_id_is_rejected(instance_id: str) -> None:
    """instance ID 只能进入既有 RabbitMQ 最小权限命名空间。"""
    with pytest.raises(ValueError, match='instance_id'):
        RabbitMQRealtimeWakeupBus(
            config=RabbitMQTestSettings(),
            instance_id=instance_id,
        )


def test_factory_selects_only_configured_active_bus() -> None:
    """active bus 由配置唯一选择，默认 Redis 路径不连接 RabbitMQ。"""
    rabbit_config = RabbitMQTestSettings()
    redis_config = RabbitMQTestSettings(HASN_REALTIME_BUS='redis')

    assert isinstance(
        build_realtime_wakeup_bus(config=rabbit_config),
        RabbitMQRealtimeWakeupBus,
    )
    assert isinstance(
        build_realtime_wakeup_bus(config=redis_config),
        RedisRealtimeWakeupBus,
    )


def test_factory_builds_observe_only_shadow_and_rejects_dual_active() -> None:
    """shadow 只允许挂在 Redis active 后，Rabbit active 禁止重复消费。"""
    shadow_config = RabbitMQTestSettings(
        HASN_REALTIME_BUS='redis',
        HASN_REALTIME_SHADOW_RABBITMQ=True,
    )
    invalid_config = RabbitMQTestSettings(
        HASN_REALTIME_BUS='rabbitmq',
        HASN_REALTIME_SHADOW_RABBITMQ=True,
    )

    shadow = build_realtime_wakeup_bus(config=shadow_config)
    assert isinstance(shadow, ShadowRealtimeWakeupBus)
    source = inspect.getsource(ShadowRealtimeWakeupBus)
    readiness_source = inspect.getsource(wait_realtime_wakeup_ready)
    assert 'self._active.start(handler)' in source
    assert 'self._shadow.start(self._observe_only)' in source
    assert 'isinstance(bus, ShadowRealtimeWakeupBus)' in readiness_source
    assert 'await bus.wait_shadow_ready()' in readiness_source

    with pytest.raises(ValueError, match='HASN_REALTIME_SHADOW_RABBITMQ'):
        build_realtime_wakeup_bus(config=invalid_config)


def test_adapter_declares_robust_fanout_and_non_requeue_ack() -> None:
    """拓扑与 ACK 语义必须满足 RabbitMQ 4.3 的临时队列约束。"""
    source = inspect.getsource(RabbitMQRealtimeWakeupBus)

    assert 'aio_pika.connect_robust' in source
    assert 'aio_pika.ExchangeType.FANOUT' in source
    assert 'exclusive=True' in source
    assert 'auto_delete=True' in source
    assert "'x-expires': REALTIME_QUEUE_EXPIRES_MS" in source
    assert 'message.process(requeue=False)' in source


def test_realtime_transport_metrics_use_only_low_cardinality_labels() -> None:
    """发布、消费、格式错误和延迟指标不得包含资源 ID label。"""
    from backend.app.hasn_im.observability import metrics

    expected = {
        metrics.HASN_REALTIME_WAKEUP_PUBLISH_TOTAL: {'transport', 'result'},
        metrics.HASN_REALTIME_WAKEUP_CONSUME_TOTAL: {'transport', 'result'},
        metrics.HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL: {'transport'},
        metrics.HASN_REALTIME_WAKEUP_LATENCY_SECONDS: {'transport'},
        metrics.HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL: {'result'},
        metrics.HASN_RABBITMQ_DELIVERY_ACK_TOTAL: {'result'},
        metrics.HASN_RABBITMQ_REDELIVERY_TOTAL: set(),
    }

    for metric, labels in expected.items():
        assert set(metric._labelnames) == labels
        assert metric in metrics.IM_METRICS


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv('RABBITMQ_REALTIME_E2E') != '1',
    reason='仅在显式提供真实 RabbitMQ 时运行',
)
async def test_real_rabbitmq_fanout_to_four_workers_and_restart() -> None:
    """真实 RabbitMQ 向四个 worker 恰好分发一次，并允许单 worker 原位重连。"""
    suffix = uuid.uuid4().hex[:12]
    consumed_event_ids: list[set[str]] = [set() for _ in range(4)]

    def observer_for(
        index: int,
    ) -> Callable[[DecodedWakeupEvent], None]:
        def observe(event: DecodedWakeupEvent) -> None:
            consumed_event_ids[index].add(event.event_id)

        return observe

    buses = [
        RabbitMQRealtimeWakeupBus(
            instance_id=f'e2e-{suffix}-{index}',
            consume_observer=observer_for(index),
        )
        for index in range(4)
    ]
    events: list[list[dict[str, object]]] = [[] for _ in buses]

    def handler_for(
        index: int,
    ) -> Callable[[dict[str, object]], Awaitable[None]]:
        async def handle(event: dict[str, object]) -> None:  # noqa: RUF029
            events[index].append(event)

        return handle

    handlers = [handler_for(index) for index in range(4)]
    for bus, handler in zip(buses, handlers, strict=True):
        bus.start(handler)

    async def wait_for_event_count(expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if all(len(worker_events) >= expected for worker_events in events):
                return
            await asyncio.sleep(0.02)
        pytest.fail(f'真实 RabbitMQ 未在期限内向四个 worker 分发第 {expected} 个事件')

    try:
        await asyncio.gather(*(bus.wait_ready() for bus in buses))

        published_event_ids = {
            await buses[0].publish_node_wakeup_event('node-real'),
        }
        await wait_for_event_count(1)
        assert all(worker_events == [{'node_id': 'node-real'}] for worker_events in events)

        published_event_ids.add(
            await buses[0].publish_broadcast_event('FRAME'),
        )
        await wait_for_event_count(2)
        assert all(worker_events[-1] == {'broadcast': True, 'payload': 'FRAME'} for worker_events in events)

        restarted_queue = buses[3].queue_name
        await buses[3].stop()
        buses[3].start(handlers[3])
        await buses[3].wait_ready()
        assert buses[3].queue_name == restarted_queue

        published_event_ids.add(
            await buses[0].publish_node_wakeup_event('node-after-restart'),
        )
        await wait_for_event_count(3)
        assert all(worker_events[-1] == {'node_id': 'node-after-restart'} for worker_events in events)

        await asyncio.sleep(0.2)
        assert all(len(worker_events) == 3 for worker_events in events)
        assert all(event_ids == published_event_ids for event_ids in consumed_event_ids)
    finally:
        await asyncio.gather(*(bus.stop() for bus in buses))


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv('RABBITMQ_REALTIME_RESTART_E2E') != '1',
    reason='仅在显式提供可重启的隔离 RabbitMQ 容器时运行',
)
async def test_real_rabbitmq_recovers_after_isolated_broker_restart() -> None:
    """真实重启隔离 broker 后，publisher 与稳定 consumer queue 自动恢复。"""
    container_name = os.getenv('RABBITMQ_REALTIME_RESTART_CONTAINER', '')
    if container_name != 'huanxing-rabbitmq-realtime-e2e':
        pytest.fail('RabbitMQ 重启 E2E 只允许操作固定隔离测试容器')
    docker = shutil.which('docker')
    if docker is None:
        pytest.fail('RabbitMQ 重启 E2E 缺少 docker CLI')

    suffix = uuid.uuid4().hex[:12]
    events: list[dict[str, object]] = []

    async def handle(event: dict[str, object]) -> None:  # noqa: RUF029
        events.append(event)

    async def wait_for_event_count(expected: int, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if len(events) >= expected:
                return
            await asyncio.sleep(0.05)
        pytest.fail(f'隔离 RabbitMQ 重启后未在期限内分发第 {expected} 个事件')

    bus = RabbitMQRealtimeWakeupBus(
        instance_id=f'restart-e2e-{suffix}',
    )
    stable_queue_name = bus.queue_name
    bus.start(handle)
    try:
        await bus.wait_ready(timeout=20)
        await bus.publish_node_wakeup_event('node-before-restart')
        await wait_for_event_count(1, 10)

        await asyncio.to_thread(
            subprocess.run,
            [docker, 'restart', container_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        await asyncio.to_thread(
            _wait_for_real_broker_listener,
            broker_host=os.environ['REALTIME_RABBITMQ_HOST'],
            broker_port=int(os.environ['REALTIME_RABBITMQ_PORT']),
        )

        await asyncio.wait_for(
            bus.publish_node_wakeup_event('node-after-restart'),
            timeout=45,
        )
        await wait_for_event_count(2, 45)
        assert bus.queue_name == stable_queue_name
        assert events == [
            {'node_id': 'node-before-restart'},
            {'node_id': 'node-after-restart'},
        ]
    finally:
        await bus.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv('RABBITMQ_REALTIME_SHADOW_STRESS_E2E') != '1',
    reason='仅在显式允许真实 Redis/RabbitMQ 十万条 shadow 压测时运行',
)
async def test_real_shadow_reconciles_one_hundred_thousand_event_ids() -> None:
    """真实双发十万条，RabbitMQ event_id 覆盖率 100% 且用户 handler 不重复。"""
    suffix = uuid.uuid4().hex
    node_id = f'shadow-stress-{suffix}'
    event_count = 100_000
    active_deliveries = 0
    consumed_event_ids: set[str] = set()

    def observe(event: DecodedWakeupEvent) -> None:
        if event.delivery_event.get('node_id') == node_id:
            consumed_event_ids.add(event.event_id)

    async def handle(event: dict[str, object]) -> None:  # noqa: RUF029
        nonlocal active_deliveries
        if event.get('node_id') == node_id:
            active_deliveries += 1

    shadow = ShadowRealtimeWakeupBus(
        shadow_consume_observer=observe,
    )
    shadow.start(handle)
    published_event_ids: set[str] = set()
    try:
        await shadow.wait_shadow_ready(timeout=20)
        max_connections = redis_client.connection_pool.max_connections
        assert isinstance(max_connections, int)
        # 为订阅与同进程其他 Redis 操作预留至少一半连接，避免压测驱动先耗尽生产连接池。
        batch_concurrency = max(1, min(50, max_connections // 2))
        for start in range(0, event_count, batch_concurrency):
            batch_size = min(batch_concurrency, event_count - start)
            published_event_ids.update(
                await asyncio.gather(*(shadow.publish_node_wakeup_event(node_id) for _ in range(batch_size)))
            )

        deadline = asyncio.get_running_loop().time() + 300
        while asyncio.get_running_loop().time() < deadline:
            if active_deliveries >= event_count and consumed_event_ids >= published_event_ids:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(
                '十万条 shadow 对账未在期限内收敛：'
                f'active={active_deliveries} '
                f'published={len(published_event_ids)} '
                f'consumed={len(consumed_event_ids)}'
            )

        assert len(published_event_ids) == event_count
        assert active_deliveries == event_count
        assert consumed_event_ids == published_event_ids
    finally:
        await shadow.stop()
