"""HASN realtime 跨 worker 唤醒总线契约。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

WakeupHandler = Callable[[dict[str, Any]], Awaitable[None]]


@runtime_checkable
class RealtimeWakeupBus(Protocol):
    """只承载在线唤醒事件，不承载持久帧或业务事实。"""

    async def publish_node_wakeup(self, node_id: str) -> None:
        """发布指定节点的待投队列唤醒。"""
        ...

    async def publish_broadcast(self, payload_json: str) -> None:
        """发布可由事实源对账恢复的在线广播。"""
        ...

    def start(self, handler: WakeupHandler) -> None:
        """启动消费循环并把合法事件交给 handler。"""
        ...

    async def stop(self) -> None:
        """停止消费循环并释放 transport 资源。"""
        ...
