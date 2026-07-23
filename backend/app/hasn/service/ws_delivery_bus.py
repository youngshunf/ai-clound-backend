"""跨 worker 的 WebSocket 持久投递总线。

**为什么需要它**：云端以 ``fba run --workers N`` 多进程部署时，每个 worker 进程
只持有自己 ``/ws/node`` accept 到的 WS 连接（``ws_router._ws_connections`` 是**进程内**
字典）。当某个 worker 在处理 ``route_message`` / 多端同步 / 配置失效广播时，需要把帧
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
import json

from fastapi import WebSocket

from backend.common.log import log
from backend.database.redis import RedisCli, redis_client
from backend.app.hasn_im.adapters.routing.redis_presence_store import NODE_GENERATION_KEY
from backend.app.hasn_im.adapters.routing.ws_connection_registry import (
    get_connection,
    get_connection_id,
    get_ready_connection_id,
    iter_connections,
    local_nodes,
)

# 共享投递频道：所有 worker 订阅，发送方 publish
WS_DELIVERY_CHANNEL = 'hasn:ws:deliver'
PENDING_PREFIX = 'hasn:ws:pending'
PROCESSING_PREFIX = 'hasn:ws:processing'
DELIVERY_QUEUE_TTL_SECS = 7 * 86400
DELIVERY_BATCH_SIZE = 100
DELIVERY_SEND_TIMEOUT_SECS = 10.0

# 投递是核心链路，订阅循环永不主动放弃（仅随进程关闭被 cancel）；Redis 抖动时退避重连
_RECONNECT_DELAY_SECS = 2.0
_RETRY_PENDING_INTERVAL_SECS = 2.0


def _decode_payload(raw: str | bytes | None) -> dict | None:
    """解析队列帧；畸形或非对象 JSON 由调用方安全跳过。"""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class WsDeliveryBus:
    """WS 跨 worker 投递总线（单例）。"""

    _task: asyncio.Task | None = None
    _retry_task: asyncio.Task | None = None
    _drain_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def publish_to_node(node_id: str, payload_json: str) -> bool:
        """先持久化目标帧，再用 Pub/Sub 唤醒持有该节点连接的 worker。"""
        try:
            pending_key = f'{PENDING_PREFIX}:{node_id}'
            await redis_client.rpush(pending_key, payload_json)
            await redis_client.expire(pending_key, DELIVERY_QUEUE_TTL_SECS)
        except Exception as exc:
            log.error(f'[WsDeliveryBus] 持久化待投帧失败 node={node_id}: {exc!r}')
            return False

        try:
            message = json.dumps({'node_id': node_id}, ensure_ascii=False)
            await redis_client.publish(WS_DELIVERY_CHANNEL, message)
        except Exception as exc:
            # 唤醒失败不等于消息丢失：周期 drain 和节点重连都会继续消费待投队列。
            log.warning(f'[WsDeliveryBus] 投递唤醒失败，等待周期重试 node={node_id}: {exc!r}')
        return True

    @staticmethod
    async def publish_broadcast(payload_json: str) -> None:
        """把一帧广播给**所有**在线 node（每个 worker 下发其本地全部连接）。

        用于全局 ``hasn.sync.invalidate``（builtin_catalog/common_skills/platform_config）。
        """
        try:
            message = json.dumps({'broadcast': True, 'payload': payload_json}, ensure_ascii=False)
            await redis_client.publish(WS_DELIVERY_CHANNEL, message)
        except Exception as exc:
            log.warning(f'[WsDeliveryBus] publish_broadcast 失败: {exc!r}')

    @staticmethod
    async def _safe_send(ws: WebSocket, payload_json: str) -> bool:
        """限时下发单帧，返回是否已交给 WebSocket transport。"""
        try:
            await asyncio.wait_for(ws.send_text(payload_json), timeout=DELIVERY_SEND_TIMEOUT_SECS)
        except Exception as exc:
            log.debug(f'[WsDeliveryBus] 单连接下发失败（保留待重试）: {exc!r}')
            return False
        else:
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
                payload_json = await redis_client.lmove(pending_key, processing_key, 'LEFT', 'RIGHT')
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
            if not payload_json:
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

        node_id = data.get('node_id')
        if not node_id:
            return
        if get_connection(node_id) is None:
            # 连接不在本 worker，交给真正持有它的 worker 处理
            return
        # 兼容滚动发布期间仍携带 payload 的旧 publisher：先落本 worker 的持久队列。
        legacy_payload = data.get('payload')
        if legacy_payload:
            pending_key = f'{PENDING_PREFIX}:{node_id}'
            await redis_client.rpush(pending_key, legacy_payload)
            await redis_client.expire(pending_key, DELIVERY_QUEUE_TTL_SECS)
        await WsDeliveryBus.drain_node(node_id)

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
    async def subscribe_and_listen(cls) -> None:  # noqa: C901
        """订阅频道并永久监听（随进程关闭被 cancel）。"""
        while True:
            pubsub_client: RedisCli | None = None
            pubsub = None
            try:
                # 独立连接订阅，避免占用业务连接
                pubsub_client = RedisCli()
                pubsub = pubsub_client.pubsub()
                await pubsub.subscribe(WS_DELIVERY_CHANNEL)

                async for message in pubsub.listen():
                    if message.get('type') != 'message':
                        continue
                    data = _decode_payload(message.get('data'))
                    if data is None:
                        log.warning('[WsDeliveryBus] 消息格式错误')
                        continue
                    await cls._deliver_local(data)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f'[WsDeliveryBus] 订阅异常，{_RECONNECT_DELAY_SECS}s 后重连: {exc!r}')
                await asyncio.sleep(_RECONNECT_DELAY_SECS)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass
                if pubsub_client is not None:
                    try:
                        await pubsub_client.aclose()
                    except Exception:
                        pass

    @classmethod
    def start_listener(cls) -> None:
        """启动订阅任务（每个 worker 进程一份）。"""
        if cls._task is None or cls._task.done():
            cls._task = asyncio.create_task(cls.subscribe_and_listen())
        if cls._retry_task is None or cls._retry_task.done():
            cls._retry_task = asyncio.create_task(cls.retry_pending_forever())

    @classmethod
    async def stop_listener(cls) -> None:
        """停止订阅任务。"""
        tasks = [task for task in (cls._task, cls._retry_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        cls._task = None
        cls._retry_task = None


ws_delivery_bus = WsDeliveryBus()
