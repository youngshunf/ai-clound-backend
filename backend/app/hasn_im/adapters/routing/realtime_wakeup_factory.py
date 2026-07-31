"""HASN realtime wake-up bus 的 active transport 工厂。"""

from __future__ import annotations

import asyncio
import uuid

from collections.abc import Callable, Coroutine
from typing import Any, Literal, Protocol

from backend.app.hasn_im.adapters.routing.rabbitmq_realtime_wakeup_bus import (
    DecodedWakeupEvent,
    RabbitMQRealtimeSettings,
    RabbitMQRealtimeWakeupBus,
)
from backend.app.hasn_im.adapters.routing.redis_realtime_wakeup_bus import (
    RedisRealtimeWakeupBus,
)
from backend.app.hasn_im.observability.metrics import (
    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL,
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


# shadow 双发的在途上限。shadow 只用于对账，绝不允许它反过来堆积任务、
# 拖垮 active 通道；超过上限直接丢弃并计量。
SHADOW_MAX_INFLIGHT_PUBLISHES = 512


class ShadowRealtimeWakeupBus:
    """Redis 驱动用户下发，RabbitMQ 只做双发与消费观测。

    双发**必须离开用户发送热路径**：RabbitMQ 侧的连接重建、confirm 等待与
    broker 阻塞都不允许计入用户可见延迟，否则「shadow 只统计」的承诺只在
    次数维度成立、在延迟维度不成立。这里把 RabbitMQ 发布交给后台任务，
    active Redis 发布完成即返回。
    """

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
        self._inflight: set[asyncio.Task[None]] = set()

    async def publish_node_wakeup(self, node_id: str) -> None:
        """先发布 active Redis，再以相同业务意图旁路双发 RabbitMQ。"""
        await self.publish_node_wakeup_event(node_id)

    async def publish_node_wakeup_event(self, node_id: str) -> str:
        """双发定向事件并返回 RabbitMQ 对账 event_id。"""
        await self._active.publish_node_wakeup(node_id)
        event_id = str(uuid.uuid4())
        self._spawn_shadow(
            self._shadow.publish_node_wakeup_event(node_id, event_id=event_id),
            kind='定向',
            event_id=event_id,
        )
        return event_id

    async def publish_broadcast(self, payload_json: str) -> None:
        """先发布 active Redis 广播，再旁路双发 RabbitMQ 观测事件。"""
        await self.publish_broadcast_event(payload_json)

    async def publish_broadcast_event(self, payload_json: str) -> str:
        """双发广播事件并返回 RabbitMQ 对账 event_id。"""
        await self._active.publish_broadcast(payload_json)
        event_id = str(uuid.uuid4())
        self._spawn_shadow(
            self._shadow.publish_broadcast_event(payload_json, event_id=event_id),
            kind='广播',
            event_id=event_id,
        )
        return event_id

    def _spawn_shadow(
        self,
        coroutine: Coroutine[Any, Any, str],
        *,
        kind: str,
        event_id: str,
    ) -> None:
        """把 shadow 双发挪到后台任务，并对在途数量设上限。"""
        if len(self._inflight) >= SHADOW_MAX_INFLIGHT_PUBLISHES:
            coroutine.close()
            HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                transport='rabbitmq',
                result='shadow_dropped',
            ).inc()
            log.warning(
                f'[ShadowRealtimeWakeupBus] shadow 在途双发已达 {SHADOW_MAX_INFLIGHT_PUBLISHES}，'
                f'丢弃本次 {kind} 观测 event_id={event_id}'
            )
            return
        task = asyncio.create_task(self._publish_shadow(coroutine, kind=kind, event_id=event_id))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    @staticmethod
    async def _publish_shadow(
        coroutine: Coroutine[Any, Any, str],
        *,
        kind: str,
        event_id: str,
    ) -> None:
        try:
            await coroutine
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                f'[ShadowRealtimeWakeupBus] RabbitMQ {kind}双发失败，event_id={event_id} error={type(exc).__name__}'
            )

    def start(self, handler: WakeupHandler) -> None:
        """Redis 绑定真实 handler；RabbitMQ consumer 只确认与计量。"""
        self._active.start(handler)
        self._shadow.start(self._observe_only)

    async def stop(self) -> None:
        """并行停止 active 与 shadow transport，任一失败都不得漏停另一侧。"""
        for task in tuple(self._inflight):
            task.cancel()
        pending = tuple(self._inflight)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._inflight.clear()
        results = await asyncio.gather(
            self._active.stop(),
            self._shadow.stop(),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                log.warning(f'[ShadowRealtimeWakeupBus] transport 停止失败 error={type(result).__name__}')

    async def wait_shadow_ready(self, timeout: float = 10.0) -> None:
        """等待 Redis active 完成订阅；RabbitMQ shadow 就绪失败只告警。

        shadow 是观测手段，不得反过来把 RabbitMQ 变成 API 的启动硬依赖——
        否则「打开观测」本身就降低了可用性。active bus 为 RabbitMQ 时仍是硬门禁。
        """
        await self._active.wait_ready(timeout=timeout)
        try:
            await self._shadow.wait_ready(timeout=timeout)
        except Exception as exc:
            log.warning(
                f'[ShadowRealtimeWakeupBus] RabbitMQ shadow 队列未在 {timeout}s 内就绪，'
                f'继续以纯 Redis 提供服务 error={type(exc).__name__}'
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
