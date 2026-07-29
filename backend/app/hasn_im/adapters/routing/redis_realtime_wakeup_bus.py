"""HASN realtime wake-up bus 的 Redis Pub/Sub adapter。"""

from __future__ import annotations

import asyncio
import json

from collections.abc import Callable
from typing import Any

from backend.app.hasn_im.observability.metrics import (
    HASN_REALTIME_WAKEUP_CONSUME_TOTAL,
    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL,
    HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL,
)
from backend.app.hasn_im.ports.realtime_wakeup_bus import WakeupHandler
from backend.common.log import log
from backend.database.redis import RedisCli, redis_client

WS_DELIVERY_CHANNEL = 'hasn:ws:deliver'
RECONNECT_DELAY_SECS = 2.0


def _decode_event(raw: str | bytes | None) -> dict[str, Any] | None:
    """解析唤醒事件；畸形或非对象 JSON 返回空。"""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class RedisRealtimeWakeupBus:
    """保持既有消息形状的 Redis Pub/Sub 实现。"""

    def __init__(
        self,
        *,
        publisher: RedisCli | None = None,
        subscriber_factory: Callable[[], RedisCli] | None = None,
    ) -> None:
        self._publisher = publisher
        self._subscriber_factory = subscriber_factory
        self._task: asyncio.Task[None] | None = None
        self._handler: WakeupHandler | None = None
        self._ready = asyncio.Event()

    async def publish_node_wakeup(self, node_id: str) -> None:
        """发布指定节点的唤醒事件。"""
        message = json.dumps({'node_id': node_id}, ensure_ascii=False)
        await self._publish(message)

    async def publish_broadcast(self, payload_json: str) -> None:
        """发布全局在线广播。"""
        message = json.dumps(
            {'broadcast': True, 'payload': payload_json},
            ensure_ascii=False,
        )
        await self._publish(message)

    async def _publish(self, message: str) -> None:
        """发布既有 Redis 消息形状并记录低基数迁移指标。"""
        publisher = self._publisher or redis_client
        try:
            await publisher.publish(WS_DELIVERY_CHANNEL, message)
        except Exception:
            HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                transport='redis',
                result='error',
            ).inc()
            raise
        else:
            HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                transport='redis',
                result='ok',
            ).inc()

    def start(self, handler: WakeupHandler) -> None:
        """启动单个订阅任务。"""
        self._handler = handler
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._subscribe_forever())

    async def stop(self) -> None:
        """取消订阅任务并等待资源释放。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._handler = None
        self._ready.clear()

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """等待 Redis 频道完成订阅，供真实 shadow E2E 使用。"""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def _subscribe_forever(self) -> None:
        """订阅既有 Redis 频道并在瞬时故障后持续重连。"""
        while True:
            try:
                await self._subscribe_once()
            except asyncio.CancelledError:  # noqa: PERF203 — 常驻订阅必须在循环内隔离每次重连
                break
            except Exception as exc:
                log.warning(f'[RedisRealtimeWakeupBus] 订阅异常，{RECONNECT_DELAY_SECS}s 后重连: {exc!r}')
                await asyncio.sleep(RECONNECT_DELAY_SECS)

    async def _subscribe_once(self) -> None:
        """创建一次独立订阅连接，退出时始终释放资源。"""
        factory = self._subscriber_factory or RedisCli
        pubsub_client = factory()
        pubsub = None
        try:
            pubsub = pubsub_client.pubsub()
            await pubsub.subscribe(WS_DELIVERY_CHANNEL)
            self._ready.set()
            async for message in pubsub.listen():
                if message.get('type') != 'message':
                    continue
                event = _decode_event(message.get('data'))
                if event is None:
                    HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL.labels(
                        transport='redis',
                    ).inc()
                    HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                        transport='redis',
                        result='schema_error',
                    ).inc()
                    log.warning('[RedisRealtimeWakeupBus] 消息格式错误')
                    continue
                handler = self._handler
                if handler is None:
                    HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                        transport='redis',
                        result='handler_missing',
                    ).inc()
                    continue
                try:
                    await handler(event)
                except Exception:
                    HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                        transport='redis',
                        result='handler_error',
                    ).inc()
                    raise
                else:
                    HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                        transport='redis',
                        result='ok',
                    ).inc()
        finally:
            self._ready.clear()
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            try:
                await pubsub_client.aclose()
            except Exception:
                pass
