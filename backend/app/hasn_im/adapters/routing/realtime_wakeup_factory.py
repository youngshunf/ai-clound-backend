"""HASN realtime wake-up bus 的 active transport 工厂。"""

from __future__ import annotations

import asyncio
import uuid

from collections.abc import Callable
from typing import Any, Literal, Protocol

from backend.app.hasn_im.adapters.routing.rabbitmq_realtime_wakeup_bus import (
    DecodedWakeupEvent,
    RabbitMQRealtimeSettings,
    RabbitMQRealtimeWakeupBus,
)
from backend.app.hasn_im.adapters.routing.redis_realtime_wakeup_bus import (
    RedisRealtimeWakeupBus,
)
from backend.app.hasn_im.ports.realtime_wakeup_bus import (
    RealtimeWakeupBus,
    WakeupHandler,
)
from backend.common.log import log
from backend.core.conf import settings


class RealtimeWakeupSettings(RabbitMQRealtimeSettings, Protocol):
    """active transport 选择所需配置。"""

    HASN_REALTIME_BUS: Literal['rabbitmq', 'redis']
    HASN_REALTIME_SHADOW_RABBITMQ: bool


class ShadowRealtimeWakeupBus:
    """Redis 驱动用户下发，RabbitMQ 只做双发与消费观测。"""

    def __init__(
        self,
        *,
        config: RealtimeWakeupSettings = settings,
        shadow_consume_observer: Callable[[DecodedWakeupEvent], None] | None = None,
    ) -> None:
        self._active = RedisRealtimeWakeupBus()
        self._shadow = RabbitMQRealtimeWakeupBus(
            config=config,
            consume_observer=shadow_consume_observer,
        )

    async def publish_node_wakeup(self, node_id: str) -> None:
        """先发布 active Redis，再以相同业务意图双发 RabbitMQ。"""
        await self.publish_node_wakeup_event(node_id)

    async def publish_node_wakeup_event(self, node_id: str) -> str:
        """双发定向事件并返回 RabbitMQ 对账 event_id。"""
        await self._active.publish_node_wakeup(node_id)
        event_id = str(uuid.uuid4())
        try:
            await self._shadow.publish_node_wakeup_event(
                node_id,
                event_id=event_id,
            )
        except Exception as exc:
            log.warning(
                f'[ShadowRealtimeWakeupBus] RabbitMQ 定向双发失败，event_id={event_id} error={type(exc).__name__}'
            )
        return event_id

    async def publish_broadcast(self, payload_json: str) -> None:
        """先发布 active Redis 广播，再双发 RabbitMQ 观测事件。"""
        await self.publish_broadcast_event(payload_json)

    async def publish_broadcast_event(self, payload_json: str) -> str:
        """双发广播事件并返回 RabbitMQ 对账 event_id。"""
        await self._active.publish_broadcast(payload_json)
        event_id = str(uuid.uuid4())
        try:
            await self._shadow.publish_broadcast_event(
                payload_json,
                event_id=event_id,
            )
        except Exception as exc:
            log.warning(
                f'[ShadowRealtimeWakeupBus] RabbitMQ 广播双发失败，event_id={event_id} error={type(exc).__name__}'
            )
        return event_id

    def start(self, handler: WakeupHandler) -> None:
        """Redis 绑定真实 handler；RabbitMQ consumer 只确认与计量。"""
        self._active.start(handler)
        self._shadow.start(self._observe_only)

    async def stop(self) -> None:
        """并行停止 active 与 shadow transport。"""
        await asyncio.gather(
            self._active.stop(),
            self._shadow.stop(),
        )

    async def wait_shadow_ready(self, timeout: float = 10.0) -> None:
        """等待 Redis active 与 RabbitMQ shadow 均完成订阅。"""
        await asyncio.gather(
            self._active.wait_ready(timeout=timeout),
            self._shadow.wait_ready(timeout=timeout),
        )

    @staticmethod
    async def _observe_only(_event: dict[str, Any]) -> None:
        """shadow consumer 禁止触发 WebSocket send 或 pending drain。"""


def build_realtime_wakeup_bus(
    *,
    config: RealtimeWakeupSettings = settings,
) -> RealtimeWakeupBus:
    """构造唯一 active wake-up bus。"""
    if config.HASN_REALTIME_BUS == 'redis':
        if config.HASN_REALTIME_SHADOW_RABBITMQ:
            return ShadowRealtimeWakeupBus(config=config)
        return RedisRealtimeWakeupBus()
    if config.HASN_REALTIME_BUS == 'rabbitmq':
        if config.HASN_REALTIME_SHADOW_RABBITMQ:
            raise ValueError('HASN_REALTIME_BUS=rabbitmq 时必须关闭 HASN_REALTIME_SHADOW_RABBITMQ')
        return RabbitMQRealtimeWakeupBus(config=config)
    raise ValueError(f'不支持的 HASN_REALTIME_BUS：{config.HASN_REALTIME_BUS}')


async def wait_realtime_wakeup_ready(bus: RealtimeWakeupBus) -> None:
    """RabbitMQ active/shadow 模式等待临时队列就绪；纯 Redis 保持既有启动语义。"""
    if isinstance(bus, RabbitMQRealtimeWakeupBus):
        await bus.wait_ready()
    elif isinstance(bus, ShadowRealtimeWakeupBus):
        await bus.wait_shadow_ready()
