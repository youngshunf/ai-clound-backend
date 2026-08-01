"""HASN realtime wake-up port 的架构契约测试。"""

from __future__ import annotations

import inspect

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DELIVERY_BUS_FILE = PROJECT_ROOT / 'backend' / 'app' / 'hasn_im' / 'adapters' / 'routing' / 'delivery_bus.py'


def test_realtime_wakeup_port_exposes_only_minimal_operations() -> None:
    """Port 只暴露发布、广播、启动与停止四个业务操作。"""
    from backend.app.hasn_im.ports.realtime_wakeup_bus import RealtimeWakeupBus

    public_methods = {
        name
        for name, value in inspect.getmembers(
            RealtimeWakeupBus,
            predicate=inspect.isfunction,
        )
        if not name.startswith('_')
    }

    assert public_methods == {
        'publish_node_wakeup',
        'publish_broadcast',
        'start',
        'stop',
    }


def test_ws_delivery_bus_no_longer_constructs_redis_pubsub() -> None:
    """持久 LIST 保留原位，Pub/Sub 连接创建必须移入 Redis adapter。"""
    source = DELIVERY_BUS_FILE.read_text(encoding='utf-8')

    assert 'RealtimeWakeupBus' in source
    assert 'RedisCli' not in source
    assert '.pubsub(' not in source
    assert '.subscribe(' not in source
    assert 'subscribe_and_listen' not in source


def test_redis_adapter_keeps_existing_channel_and_event_shape() -> None:
    """Redis adapter 继续使用既有频道和 node/broadcast 消息形状。"""
    from backend.app.hasn_im.adapters.routing.redis_realtime_wakeup_bus import (
        WS_DELIVERY_CHANNEL,
        RedisRealtimeWakeupBus,
    )

    source = inspect.getsource(RedisRealtimeWakeupBus)

    assert WS_DELIVERY_CHANNEL == 'hasn:ws:deliver'
    assert "'node_id': node_id" in source
    assert "'broadcast': True" in source
    assert "'payload': payload_json" in source
