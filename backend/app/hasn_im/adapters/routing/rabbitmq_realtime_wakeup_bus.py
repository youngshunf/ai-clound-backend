"""HASN realtime wake-up bus 的 RabbitMQ fanout adapter。"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import aio_pika

from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractRobustConnection,
)
from aio_pika.exceptions import PublishError
from aio_pika.robust_channel import RobustChannel

from backend.app.hasn_im.observability.metrics import (
    HASN_RABBITMQ_DELIVERY_ACK_TOTAL,
    HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL,
    HASN_RABBITMQ_REDELIVERY_TOTAL,
    HASN_REALTIME_WAKEUP_CONSUME_TOTAL,
    HASN_REALTIME_WAKEUP_LATENCY_SECONDS,
    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL,
    HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL,
)
from backend.app.hasn_im.ports.realtime_wakeup_bus import WakeupHandler
from backend.common.log import log
from backend.common.messaging.rabbitmq import (
    build_amqp_dsn,
    describe_rabbitmq_endpoint,
)
from backend.common.observability.otel import (
    inject_rabbitmq_trace_headers,
    mark_messaging_span,
    rabbitmq_consume_span,
    rabbitmq_publish_span,
)
from backend.core.conf import settings

REALTIME_EXCHANGE = 'huanxing.realtime'
REALTIME_QUEUE_PREFIX = 'huanxing.realtime.worker.'
REALTIME_QUEUE_TRACE_DESTINATION = 'huanxing.realtime.worker'
REALTIME_QUEUE_EXPIRES_MS = 300_000
RECONNECT_DELAY_SECS = 2.0
# publish 位于用户消息发送的热路径上，任何等待都必须有上界：
# broker 触发 memory/disk alarm 进入 connection.blocked 时，无超时的 publish
# 会把发送请求一起挂死。唤醒丢失由 Redis pending + 周期 drain 兜底，超时远比挂起安全。
PUBLISHER_RECOVERY_TIMEOUT_SECS = 3.0
PUBLISH_TIMEOUT_SECS = 5.0
CONNECT_TIMEOUT_SECS = 5.0
EVENT_SCHEMA_VERSION = 1
_INSTANCE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_NODE_EVENT_REQUIRED_KEYS = frozenset({'version', 'event_id', 'sent_at_ms', 'kind', 'node_id'})
_BROADCAST_EVENT_REQUIRED_KEYS = frozenset({'version', 'event_id', 'sent_at_ms', 'kind', 'payload'})


class RabbitMQRealtimeSettings(Protocol):
    """RabbitMQ realtime adapter 所需的最小配置契约。"""

    REALTIME_RABBITMQ_HOST: str
    REALTIME_RABBITMQ_PORT: int
    REALTIME_RABBITMQ_VHOST: str
    REALTIME_RABBITMQ_USERNAME: str
    REALTIME_RABBITMQ_PASSWORD: str


@dataclass(frozen=True, slots=True)
class DecodedWakeupEvent:
    """校验通过的 RabbitMQ 唤醒事件。"""

    event_id: str
    sent_at_ms: int
    delivery_event: dict[str, Any]


def _event_metadata(
    *,
    event_id: str | None,
    sent_at_ms: int | None,
) -> tuple[str, int]:
    selected_event_id = event_id or str(uuid.uuid4())
    selected_sent_at_ms = sent_at_ms or time.time_ns() // 1_000_000
    return selected_event_id, selected_sent_at_ms


def encode_node_wakeup(
    node_id: str,
    *,
    event_id: str | None = None,
    sent_at_ms: int | None = None,
) -> bytes:
    """编码定向唤醒事件。"""
    selected_event_id, selected_sent_at_ms = _event_metadata(
        event_id=event_id,
        sent_at_ms=sent_at_ms,
    )
    return json.dumps(
        {
            'version': EVENT_SCHEMA_VERSION,
            'event_id': selected_event_id,
            'sent_at_ms': selected_sent_at_ms,
            'kind': 'node',
            'node_id': node_id,
        },
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode()


def encode_broadcast_wakeup(
    payload_json: str,
    *,
    event_id: str | None = None,
    sent_at_ms: int | None = None,
) -> bytes:
    """编码在线广播唤醒事件。"""
    selected_event_id, selected_sent_at_ms = _event_metadata(
        event_id=event_id,
        sent_at_ms=sent_at_ms,
    )
    return json.dumps(
        {
            'version': EVENT_SCHEMA_VERSION,
            'event_id': selected_event_id,
            'sent_at_ms': selected_sent_at_ms,
            'kind': 'broadcast',
            'payload': payload_json,
        },
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode()


def _valid_metadata(value: dict[str, Any]) -> bool:
    event_id = value.get('event_id')
    sent_at_ms = value.get('sent_at_ms')
    if value.get('version') != EVENT_SCHEMA_VERSION:
        return False
    if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
        return False
    return not (not isinstance(sent_at_ms, int) or isinstance(sent_at_ms, bool) or sent_at_ms <= 0)


def decode_wakeup_event(body: bytes) -> DecodedWakeupEvent | None:
    """校验 RabbitMQ 唤醒 schema，并还原既有 delivery 事件。

    只要求必需键齐全，**忽略未识别的新增键**：滚动发布期间新 publisher 补字段时，
    旧 worker 必须继续消费，否则一次加字段就是一整轮全量丢帧。不兼容变更只能通过
    提升 `version` 表达（`_valid_metadata` 精确比对版本号）。
    """
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict) or not _valid_metadata(value):
        return None

    kind = value.get('kind')
    delivery_event: dict[str, Any]
    if kind == 'node':
        if not _NODE_EVENT_REQUIRED_KEYS <= set(value):
            return None
        node_id = value.get('node_id')
        if not isinstance(node_id, str) or not node_id:
            return None
        delivery_event = {'node_id': node_id}
    elif kind == 'broadcast':
        if not _BROADCAST_EVENT_REQUIRED_KEYS <= set(value):
            return None
        payload = value.get('payload')
        if not isinstance(payload, str):
            return None
        delivery_event = {'broadcast': True, 'payload': payload}
    else:
        return None

    return DecodedWakeupEvent(
        event_id=value['event_id'],
        sent_at_ms=value['sent_at_ms'],
        delivery_event=delivery_event,
    )


class RabbitMQRealtimeWakeupBus:
    """基于 robust connection 和每 worker 临时队列的 fanout 实现。"""

    def __init__(
        self,
        *,
        config: RabbitMQRealtimeSettings = settings,
        instance_id: str | None = None,
        consume_observer: Callable[[DecodedWakeupEvent], None] | None = None,
    ) -> None:
        selected_instance_id = uuid.uuid4().hex if instance_id is None else instance_id
        if _INSTANCE_ID_PATTERN.fullmatch(selected_instance_id) is None:
            raise ValueError('RabbitMQ realtime instance_id 必须为 1–64 位字母、数字、下划线或连字符')

        self.instance_id = selected_instance_id
        self.queue_name = f'{REALTIME_QUEUE_PREFIX}{selected_instance_id}'
        self._dsn = build_amqp_dsn(
            host=config.REALTIME_RABBITMQ_HOST,
            port=config.REALTIME_RABBITMQ_PORT,
            username=config.REALTIME_RABBITMQ_USERNAME,
            password=config.REALTIME_RABBITMQ_PASSWORD,
            vhost=config.REALTIME_RABBITMQ_VHOST,
        )
        self._endpoint = describe_rabbitmq_endpoint(
            host=config.REALTIME_RABBITMQ_HOST,
            port=config.REALTIME_RABBITMQ_PORT,
            vhost=config.REALTIME_RABBITMQ_VHOST,
        )
        self._handler: WakeupHandler | None = None
        self._consume_observer = consume_observer
        self._consumer_task: asyncio.Task[None] | None = None
        self._consumer_connection: AbstractRobustConnection | None = None
        self._consumer_channel: AbstractChannel | None = None
        self._publisher_connection: AbstractRobustConnection | None = None
        self._publisher_channel: RobustChannel | None = None
        self._publisher_exchange: AbstractExchange | None = None
        self._publisher_lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def publish_node_wakeup(self, node_id: str) -> None:
        """向 fanout exchange 发布定向唤醒。"""
        await self.publish_node_wakeup_event(node_id)

    async def publish_broadcast(self, payload_json: str) -> None:
        """向 fanout exchange 发布在线广播。"""
        await self.publish_broadcast_event(payload_json)

    async def publish_node_wakeup_event(
        self,
        node_id: str,
        *,
        event_id: str | None = None,
    ) -> str:
        """发布可由 shadow 测试按 event_id 对账的定向事件。"""
        selected_event_id = event_id or str(uuid.uuid4())
        await self._publish(
            encode_node_wakeup(node_id, event_id=selected_event_id),
        )
        return selected_event_id

    async def publish_broadcast_event(
        self,
        payload_json: str,
        *,
        event_id: str | None = None,
    ) -> str:
        """发布可由 shadow 测试按 event_id 对账的广播事件。"""
        selected_event_id = event_id or str(uuid.uuid4())
        await self._publish(
            encode_broadcast_wakeup(payload_json, event_id=selected_event_id),
        )
        return selected_event_id

    def start(self, handler: WakeupHandler) -> None:
        """启动单个 robust 消费任务。"""
        self._handler = handler
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_forever())

    async def stop(self) -> None:
        """停止消费并有序关闭 consumer、channel 和 connection。"""
        if self._consumer_task is not None and not self._consumer_task.done():
            self._consumer_task.cancel()
            await asyncio.gather(self._consumer_task, return_exceptions=True)
        self._consumer_task = None
        self._handler = None
        self._ready.clear()
        await self._close_publisher()
        await self._close_consumer()

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """等待临时队列完成声明和绑定，供启动门禁与真实 E2E 使用。"""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def _publish(self, body: bytes) -> None:
        with rabbitmq_publish_span(REALTIME_EXCHANGE) as span:
            message = aio_pika.Message(
                body=body,
                content_type='application/json',
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
                headers=inject_rabbitmq_trace_headers(),
            )
            for attempt in range(2):
                exchange: AbstractExchange | None = None
                try:
                    exchange = await self._publisher()
                    await exchange.publish(
                        message,
                        routing_key='',
                        timeout=PUBLISH_TIMEOUT_SECS,
                    )
                except PublishError as exc:
                    # fanout 上暂时没有任何绑定队列（API worker 全部重启中、或本进程
                    # 只发不收）。这是预期状态而非连接故障：帧仍在 Redis pending 里，
                    # 由重连握手与周期 drain 兜底。不得重建连接、不得重试。
                    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                        transport='rabbitmq',
                        result='returned',
                    ).inc()
                    HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL.labels(
                        result='returned',
                    ).inc()
                    mark_messaging_span(span, result='returned', error=exc)
                    log.warning(
                        '[RabbitMQRealtimeWakeupBus] 唤醒无可路由队列，'
                        f'等待 pending drain 兜底 {self._endpoint}'
                    )
                    return
                except Exception as exc:
                    if attempt == 0 and exchange is not None:
                        # confirm 失败时交付状态可能不确定；同一 event_id 最多重放一次。
                        # 唤醒不承载业务事实，重复由 pending/generation 语义幂等收敛。
                        # 这里位于用户发送热路径，重建后立即重放，不做退避等待。
                        span.set_attribute('hasn.messaging.retried', True)
                        log.warning(
                            '[RabbitMQRealtimeWakeupBus] publisher 连接失效，'
                            f'重建后重放一次 {self._endpoint} error={type(exc).__name__}'
                        )
                        await self._reset_publisher_if_current(exchange)
                        continue
                    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                        transport='rabbitmq',
                        result='error',
                    ).inc()
                    HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL.labels(
                        result='error',
                    ).inc()
                    mark_messaging_span(span, result='confirm_error', error=exc)
                    raise
                else:
                    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL.labels(
                        transport='rabbitmq',
                        result='ok',
                    ).inc()
                    HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL.labels(
                        result='confirmed',
                    ).inc()
                    mark_messaging_span(span, result='confirmed')
                    return

    async def _publisher(self) -> AbstractExchange:
        async with self._publisher_lock:
            exchange = self._publisher_exchange
            channel = self._publisher_channel
            connection = self._publisher_connection
            if exchange is not None and channel is not None and connection is not None:
                if not connection.is_closed:
                    try:
                        await asyncio.wait_for(
                            channel.ready(),
                            timeout=PUBLISHER_RECOVERY_TIMEOUT_SECS,
                        )
                    except Exception as exc:
                        log.warning(
                            '[RabbitMQRealtimeWakeupBus] publisher robust channel 未恢复，'
                            f'改为重建 {self._endpoint} error={type(exc).__name__}'
                        )
                    else:
                        return exchange
                self._publisher_exchange = None
                self._publisher_channel = None
                self._publisher_connection = None
                await self._close_channel_and_connection(channel, connection)

            connection = await aio_pika.connect_robust(self._dsn, timeout=CONNECT_TIMEOUT_SECS)
            channel = cast(
                'RobustChannel',
                # `on_return_raises=True` 让不可路由的 mandatory 消息抛 `PublishError`，
                # 否则 aiormq 会把被退回的消息当作正常确认返回，指标上「confirmed」
                # 会把丢弃的唤醒一起算进去。
                await connection.channel(publisher_confirms=True, on_return_raises=True),
            )
            exchange = await channel.declare_exchange(
                REALTIME_EXCHANGE,
                aio_pika.ExchangeType.FANOUT,
                durable=True,
                auto_delete=False,
            )
            self._publisher_connection = connection
            self._publisher_channel = channel
            self._publisher_exchange = exchange
            return exchange

    async def _reset_publisher_if_current(
        self,
        failed_exchange: AbstractExchange,
    ) -> None:
        """仅回收当前失败的 publisher，避免并发恢复关闭新连接。"""
        async with self._publisher_lock:
            if self._publisher_exchange is not failed_exchange:
                return
            channel = self._publisher_channel
            connection = self._publisher_connection
            self._publisher_exchange = None
            self._publisher_channel = None
            self._publisher_connection = None
        await self._close_channel_and_connection(channel, connection)

    async def _consume_forever(self) -> None:
        while True:
            try:
                await self._consume_once()
            except asyncio.CancelledError:  # noqa: PERF203 — 常驻 consumer 必须在循环内隔离重连
                break
            except Exception as exc:
                self._ready.clear()
                log.warning(
                    '[RabbitMQRealtimeWakeupBus] consumer 异常，'
                    f'{RECONNECT_DELAY_SECS}s 后重连 '
                    f'{self._endpoint} error={type(exc).__name__}'
                )
                await self._close_consumer()
                await asyncio.sleep(RECONNECT_DELAY_SECS)

    async def _consume_once(self) -> None:
        connection = await aio_pika.connect_robust(self._dsn, timeout=10)
        self._consumer_connection = connection
        try:
            channel = await connection.channel()
            self._consumer_channel = channel
            await channel.set_qos(prefetch_count=100)
            exchange = await channel.declare_exchange(
                REALTIME_EXCHANGE,
                aio_pika.ExchangeType.FANOUT,
                durable=True,
                auto_delete=False,
            )
            queue = await channel.declare_queue(
                self.queue_name,
                durable=False,
                exclusive=True,
                auto_delete=True,
                arguments={'x-expires': REALTIME_QUEUE_EXPIRES_MS},
            )
            await queue.bind(exchange)
            self._ready.set()
            async with queue.iterator() as iterator:
                async for message in iterator:
                    await self._consume_message(message)
        finally:
            self._ready.clear()
            if self._consumer_connection is connection:
                await self._close_consumer()

    async def _consume_message(self, message: AbstractIncomingMessage) -> None:
        redelivered = bool(message.redelivered)
        if redelivered:
            HASN_RABBITMQ_REDELIVERY_TOTAL.inc()
        with rabbitmq_consume_span(
            destination=REALTIME_QUEUE_TRACE_DESTINATION,
            headers=message.headers,
            redelivered=redelivered,
        ) as span:
            result = 'ok'
            processing_error: Exception | None = None
            try:
                async with message.process(requeue=False):
                    event = decode_wakeup_event(message.body)
                    if event is None:
                        result = 'schema_error'
                        HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL.labels(
                            transport='rabbitmq',
                        ).inc()
                        HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                            transport='rabbitmq',
                            result=result,
                        ).inc()
                        log.warning('[RabbitMQRealtimeWakeupBus] 丢弃并确认 schema 非法的唤醒事件')
                    else:
                        latency_seconds = max(
                            (time.time_ns() // 1_000_000 - event.sent_at_ms) / 1000,
                            0.0,
                        )
                        HASN_REALTIME_WAKEUP_LATENCY_SECONDS.labels(
                            transport='rabbitmq',
                        ).observe(latency_seconds)
                        observer = self._consume_observer
                        if observer is not None:
                            try:
                                observer(event)
                            except Exception as exc:
                                log.warning(
                                    '[RabbitMQRealtimeWakeupBus] consume observer 失败 '
                                    f'event_id={event.event_id} error={type(exc).__name__}'
                                )
                        handler = self._handler
                        if handler is None:
                            result = 'handler_missing'
                            HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                                transport='rabbitmq',
                                result=result,
                            ).inc()
                            log.warning('[RabbitMQRealtimeWakeupBus] consumer 未绑定 handler，事件已确认')
                        else:
                            try:
                                await handler(event.delivery_event)
                            except Exception as exc:
                                result = 'handler_error'
                                processing_error = exc
                                HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                                    transport='rabbitmq',
                                    result=result,
                                ).inc()
                                log.warning(
                                    '[RabbitMQRealtimeWakeupBus] handler 失败，事件不重入队 '
                                    f'event_id={event.event_id} error={type(exc).__name__}'
                                )
                            else:
                                HASN_REALTIME_WAKEUP_CONSUME_TOTAL.labels(
                                    transport='rabbitmq',
                                    result=result,
                                ).inc()
            except Exception as exc:
                HASN_RABBITMQ_DELIVERY_ACK_TOTAL.labels(result='error').inc()
                mark_messaging_span(span, result='ack_error', error=exc)
                raise
            else:
                HASN_RABBITMQ_DELIVERY_ACK_TOTAL.labels(result='acked').inc()
                mark_messaging_span(
                    span,
                    result=result,
                    error=processing_error,
                    failed=result != 'ok',
                )

    async def _close_consumer(self) -> None:
        channel = self._consumer_channel
        connection = self._consumer_connection
        self._consumer_channel = None
        self._consumer_connection = None
        if channel is not None and not channel.is_closed:
            await channel.close()
        if connection is not None and not connection.is_closed:
            await connection.close()

    async def _close_publisher(self) -> None:
        async with self._publisher_lock:
            channel = self._publisher_channel
            connection = self._publisher_connection
            self._publisher_exchange = None
            self._publisher_channel = None
            self._publisher_connection = None
        await self._close_channel_and_connection(channel, connection)

    @staticmethod
    async def _close_channel_and_connection(
        channel: AbstractChannel | None,
        connection: AbstractRobustConnection | None,
    ) -> None:
        """关闭一组已从共享状态摘除的 publisher 资源。"""
        if channel is not None and not channel.is_closed:
            await channel.close()
        if connection is not None and not connection.is_closed:
            await connection.close()
