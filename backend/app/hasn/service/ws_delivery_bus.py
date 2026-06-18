"""跨 worker 的 WebSocket 投递总线（Redis pub/sub fan-out）。

**为什么需要它**：云端以 ``fba run --workers N`` 多进程部署时，每个 worker 进程
只持有自己 ``/ws/node`` accept 到的 WS 连接（``ws_router._ws_connections`` 是**进程内**
字典）。当某个 worker 在处理 ``route_message`` / 多端同步 / 配置失效广播时，需要把帧
投给一个连接落在**别的 worker** 的 node，本进程查 ``_ws_connections`` 必然 miss——
旧实现把帧 ``rpush`` 进 ``hasn:push:{node_id}`` 这个**没有任何消费者**的 Redis 队列，
消息永久丢失（且因为返回 pushed=True 连离线队列都不进），表现为「多 worker 下完全
收不到消息」。单 worker 部署看不到此问题（所有连接都在唯一进程里）。

**机制**：每个 worker 启动期订阅共享频道 ``hasn:ws:deliver``；发送方把
``{node_id, payload}``（或广播 ``{broadcast, payload}``）publish 上去；每个 worker 的
订阅者收到后检查自己是否持有该 node 的连接，持有就 ``send_text`` 下发，否则忽略。
由于一个 node 的 socket 只会落在唯一一个 worker，不会重复下发；单 worker 时同样成立
（自己 publish 自己消费）。presence（在线/离线判定）仍以 Redis 为权威，本总线只负责
「在线 node 的实时跨进程投递」，离线兜底仍走离线队列 + 重连握手补推。
"""

import asyncio
import json

from fastapi import WebSocket

from backend.common.log import log
from backend.database.redis import RedisCli, redis_client

# 共享投递频道：所有 worker 订阅，发送方 publish
WS_DELIVERY_CHANNEL = 'hasn:ws:deliver'

# 投递是核心链路，订阅循环永不主动放弃（仅随进程关闭被 cancel）；Redis 抖动时退避重连
_RECONNECT_DELAY_SECS = 2.0


class WsDeliveryBus:
    """WS 跨 worker 投递总线（单例）。"""

    _task: asyncio.Task | None = None

    @staticmethod
    async def publish_to_node(node_id: str, payload_json: str) -> None:
        """把一帧投给某个 node：经 Redis 广播，持有该连接的 worker 会下发。"""
        try:
            message = json.dumps({'node_id': node_id, 'payload': payload_json}, ensure_ascii=False)
            await redis_client.publish(WS_DELIVERY_CHANNEL, message)
        except Exception as exc:
            log.warning(f'[WsDeliveryBus] publish_to_node 失败 node={node_id}: {exc!r}')

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
    async def _safe_send(ws: WebSocket, payload_json: str) -> None:
        """向单个连接下发，吞掉异常：单连接坏掉不影响其它（离线/同步兜底）。"""
        try:
            await ws.send_text(payload_json)
        except Exception as exc:
            log.debug(f'[WsDeliveryBus] 单连接下发失败（忽略）: {exc!r}')

    @staticmethod
    async def _deliver_local(data: dict) -> None:
        """订阅者回调：仅下发本 worker 持有的连接。"""
        # 延迟 import 打破与 ws_router 的循环依赖（ws_router 在模块顶层 import 本总线）
        from backend.app.hasn.service.ws_router import _ws_connections

        payload_json = data.get('payload')
        if not payload_json:
            return

        if data.get('broadcast'):
            for ws in list(_ws_connections.values()):
                await WsDeliveryBus._safe_send(ws, payload_json)
            return

        node_id = data.get('node_id')
        if not node_id:
            return
        ws = _ws_connections.get(node_id)
        if ws is None:
            # 连接不在本 worker，交给真正持有它的 worker 处理
            return
        await WsDeliveryBus._safe_send(ws, payload_json)

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
                    try:
                        data = json.loads(message['data'])
                    except (json.JSONDecodeError, TypeError) as exc:
                        log.warning(f'[WsDeliveryBus] 消息格式错误: {exc!r}')
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

    @classmethod
    async def stop_listener(cls) -> None:
        """停止订阅任务。"""
        if cls._task is None:
            return
        if not cls._task.done():
            cls._task.cancel()
            try:
                await cls._task
            except asyncio.CancelledError:
                pass
        cls._task = None


ws_delivery_bus = WsDeliveryBus()
