"""跨 worker 的 WebSocket 持久投递总线。

**为什么需要它**：云端以 ``fba run --workers N`` 多进程部署时，每个 worker 进程
只持有自己 ``/ws/node`` accept 到的 WS 连接（连接注册表是**进程内**字典）。当某个 worker
在处理消息、新旧端同步或配置失效广播时，需要把帧
投给一个连接落在**别的 worker** 的 node，本进程查 ``_ws_connections`` 必然 miss——
旧实现把帧 ``rpush`` 进 ``hasn:push:{node_id}`` 这个**没有任何消费者**的 Redis 队列，
消息永久丢失（且因为返回 pushed=True 连离线队列都不进），表现为「多 worker 下完全
收不到消息」。单 worker 部署看不到此问题（所有连接都在唯一进程里）。

**机制**：定向帧先进入 Redis 待投队列，再通过共享频道 ``hasn:ws:deliver`` 唤醒持有
当前 node 连接的 worker；发送成功后才从 processing 队列确认删除。Pub/Sub 只负责降低
延迟，即使订阅短暂中断，周期 drain 和重连握手仍会继续消费。真正发送前同时校验 Redis
连接代际与本 worker 的 ready 代际，确保 ``hasn.connected`` 永远是首帧，旧 socket 也不能
消费新连接的消息。广播帧用于可由 revision 对账恢复的失效通知，仍是 best-effort。
"""

import asyncio

from typing import Literal

from fastapi import WebSocket

from backend.app.hasn_im.adapters.routing.realtime_wakeup_factory import (
    build_realtime_wakeup_bus,
    wait_realtime_wakeup_ready,
)
from backend.app.hasn_im.adapters.routing.redis_presence_store import NODE_GENERATION_KEY
from backend.app.hasn_im.adapters.routing.ws_connection_registry import (
    get_connection,
    get_connection_id,
    get_ready_connection_id,
    iter_connections,
    local_nodes,
)
from backend.app.hasn_im.ports.realtime_wakeup_bus import RealtimeWakeupBus
from backend.common.log import log
from backend.common.observability.otel import mark_messaging_span, websocket_send_span
from backend.core.conf import settings
from backend.database.redis import redis_client

PENDING_PREFIX = 'hasn:ws:pending'
PROCESSING_PREFIX = 'hasn:ws:processing'
DELIVERY_QUEUE_TTL_SECS = 7 * 86400
DELIVERY_BATCH_SIZE = 100
DELIVERY_SEND_TIMEOUT_SECS = 10.0

_RETRY_PENDING_INTERVAL_SECS = 2.0

# Redis 6.0 没有 LMOVE；兼容路径用 Lua 保持“队头取出并追加到处理中队尾”的原子语义。
_MOVE_PENDING_TO_PROCESSING_LUA = """
local payload = redis.call('LPOP', KEYS[1])
if payload then
    redis.call('RPUSH', KEYS[2], payload)
end
return payload
"""

RedisListMoveMode = Literal['lua', 'lmove']


async def _move_pending_to_processing(
    pending_key: str,
    processing_key: str,
    *,
    move_mode: RedisListMoveMode | None = None,
) -> str | None:
    """按显式模式原子领取一条待投帧，并保持 FIFO。"""
    selected_mode = settings.REDIS_LIST_MOVE_MODE if move_mode is None else move_mode
    if selected_mode == 'lmove':
        return await redis_client.lmove(
            pending_key,
            processing_key,
            src='LEFT',
            dest='RIGHT',
        )
    return await redis_client.eval(
        _MOVE_PENDING_TO_PROCESSING_LUA,
        2,
        pending_key,
        processing_key,
    )


class WsDeliveryBus:
    """WS 跨 worker 投递总线（单例）。"""

    _wakeup_bus: RealtimeWakeupBus | None = None
    _retry_task: asyncio.Task[None] | None = None
    _drain_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_wakeup_bus(cls) -> RealtimeWakeupBus:
        """在 worker 进程内惰性构造，避免预加载后复制相同 RabbitMQ instance ID。"""
        if cls._wakeup_bus is None:
            cls._wakeup_bus = build_realtime_wakeup_bus()
        return cls._wakeup_bus

    @classmethod
    async def publish_to_node(cls, node_id: str, payload_json: str) -> bool:
        """先持久化目标帧，再用 Pub/Sub 唤醒持有该节点连接的 worker。"""
        try:
            pending_key = f'{PENDING_PREFIX}:{node_id}'
            await redis_client.rpush(pending_key, payload_json)
            await redis_client.expire(pending_key, DELIVERY_QUEUE_TTL_SECS)
        except Exception as exc:
            log.error(f'[WsDeliveryBus] 持久化待投帧失败 node={node_id}: {exc!r}')
            return False

        try:
            await cls._get_wakeup_bus().publish_node_wakeup(node_id)
        except Exception as exc:
            # 唤醒失败不等于消息丢失：周期 drain 和节点重连都会继续消费待投队列。
            log.warning(f'[WsDeliveryBus] 投递唤醒失败，等待周期重试 node={node_id}: {exc!r}')
        return True

    @classmethod
    async def publish_broadcast(cls, payload_json: str) -> None:
        """把一帧广播给**所有**在线 node（每个 worker 下发其本地全部连接）。

        用于全局 ``hasn.sync.invalidate``（builtin_catalog/common_skills/platform_config）。
        """
        try:
            await cls._get_wakeup_bus().publish_broadcast(payload_json)
        except Exception as exc:
            log.warning(f'[WsDeliveryBus] publish_broadcast 失败: {exc!r}')

    @staticmethod
    async def _safe_send(ws: WebSocket, payload_json: str) -> bool:
        """限时下发单帧，返回是否已交给 WebSocket transport。"""
        with websocket_send_span() as span:
            try:
                await asyncio.wait_for(
                    ws.send_text(payload_json),
                    timeout=DELIVERY_SEND_TIMEOUT_SECS,
                )
            except Exception as exc:
                mark_messaging_span(span, result='send_error', error=exc)
                log.debug(f'[WsDeliveryBus] 单连接下发失败（保留待重试）: {exc!r}')
                return False
            else:
                mark_messaging_span(span, result='sent')
                return True

    @classmethod
    async def drain_node(cls, node_id: str) -> int:
        """当前连接代际消费 node 的持久待投队列，成功发送后才确认删除。"""
        ws = get_connection(node_id)
        connection_id = get_connection_id(node_id)
        ready_id = get_ready_connection_id(node_id)
        if ws is None or not connection_id or ready_id != connection_id:
            return 0

        lock = cls._drain_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            if (
                get_connection(node_id) is not ws
                or get_connection_id(node_id) != connection_id
                or get_ready_connection_id(node_id) != connection_id
                or await redis_client.hget(NODE_GENERATION_KEY, node_id) != connection_id
            ):
                return 0

            pending_key = f'{PENDING_PREFIX}:{node_id}'
            processing_key = f'{PROCESSING_PREFIX}:{node_id}'

            # 上一次发送在 ACK 前中断时，processing 中的帧重新入队；重复投递由消息 id 幂等。
            for _ in range(DELIVERY_BATCH_SIZE):
                recovered = await redis_client.rpoplpush(processing_key, pending_key)
                if recovered is None:
                    break

            delivered = 0
            for _ in range(DELIVERY_BATCH_SIZE):
                payload_json = await _move_pending_to_processing(pending_key, processing_key)
                if payload_json is None:
                    break
                # drain 期间可能已有同 node 的新连接在其它 worker 抢占代际；旧 socket
                # 不得继续消费。条目已在 processing，下一代 drain 会先恢复它。
                if (
                    get_connection(node_id) is not ws
                    or get_connection_id(node_id) != connection_id
                    or get_ready_connection_id(node_id) != connection_id
                    or await redis_client.hget(NODE_GENERATION_KEY, node_id) != connection_id
                ):
                    break
                if not await cls._safe_send(ws, payload_json):
                    break
                await redis_client.lrem(processing_key, 1, payload_json)
                delivered += 1

            await redis_client.expire(pending_key, DELIVERY_QUEUE_TTL_SECS)
            await redis_client.expire(processing_key, DELIVERY_QUEUE_TTL_SECS)
            return delivered

    @staticmethod
    async def _deliver_local(data: dict) -> None:
        """订阅者回调：仅下发本 worker 持有的连接。"""
        if data.get('broadcast'):
            payload_json = data.get('payload')
            if not isinstance(payload_json, str) or not payload_json:
                return
            for node_id, ws in iter_connections():
                connection_id = get_connection_id(node_id)
                if (
                    not connection_id
                    or get_ready_connection_id(node_id) != connection_id
                    or await redis_client.hget(NODE_GENERATION_KEY, node_id) != connection_id
                ):
                    continue
                await WsDeliveryBus._safe_send(ws, payload_json)
            return

        target_node_id = data.get('node_id')
        if not isinstance(target_node_id, str) or not target_node_id:
            return
        if get_connection(target_node_id) is None:
            # 连接不在本 worker，交给真正持有它的 worker 处理
            return
        # 兼容滚动发布期间仍携带 payload 的旧 publisher：先落本 worker 的持久队列。
        legacy_payload = data.get('payload')
        if isinstance(legacy_payload, str) and legacy_payload:
            pending_key = f'{PENDING_PREFIX}:{target_node_id}'
            await redis_client.rpush(pending_key, legacy_payload)
            await redis_client.expire(pending_key, DELIVERY_QUEUE_TTL_SECS)
        await WsDeliveryBus.drain_node(target_node_id)

    @classmethod
    async def _drain_node_safely(cls, node_id: str) -> None:
        try:
            await cls.drain_node(node_id)
        except Exception as exc:
            log.warning(f'[WsDeliveryBus] 周期重试失败 node={node_id}: {exc!r}')

    @classmethod
    async def retry_pending_forever(cls) -> None:
        """周期扫描本 worker 连接，弥补 Pub/Sub 唤醒窗口中的漏通知。"""
        while True:
            await asyncio.sleep(_RETRY_PENDING_INTERVAL_SECS)
            for node_id in local_nodes():
                await cls._drain_node_safely(node_id)

    @classmethod
    def start_listener(cls) -> None:
        """启动订阅任务（每个 worker 进程一份）。"""
        cls._get_wakeup_bus().start(cls._deliver_local)
        if cls._retry_task is None or cls._retry_task.done():
            cls._retry_task = asyncio.create_task(cls.retry_pending_forever())

    @classmethod
    async def wait_listener_ready(cls) -> None:
        """在 RabbitMQ active 模式等待每 worker 临时队列完成绑定。"""
        await wait_realtime_wakeup_ready(cls._get_wakeup_bus())

    @classmethod
    async def stop_listener(cls) -> None:
        """停止订阅任务。"""
        wakeup_bus = cls._wakeup_bus
        if wakeup_bus is not None:
            await wakeup_bus.stop()
        cls._wakeup_bus = None
        if cls._retry_task is not None and not cls._retry_task.done():
            cls._retry_task.cancel()
            await asyncio.gather(cls._retry_task, return_exceptions=True)
        cls._retry_task = None


ws_delivery_bus = WsDeliveryBus()
