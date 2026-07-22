"""hasn_im.ports.realtime_gateway · RealtimeGateway 契约（§7.3-2）

realtime_notifier 消费者经此 port 把可重复的在线帧送入跨 worker delivery bus。
best-effort 语义（§7.2）：帧可重复可丢，客户端按 message_id/event_id 去重（§7.4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RealtimeFrame:
    """一条实时投递帧（值对象）。method/params 为 hasn 协议帧内容。"""

    method: str
    params: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RealtimeGateway(Protocol):
    """实时投递（best-effort，不是事实源，§7.4）。"""

    async def push_to_owner(self, owner_id: str, frame: RealtimeFrame) -> None:
        """向某 owner 的全部在线节点投递一帧（离线自动入队补拉）。"""
        ...

    async def push_to_node(self, node_id: str, frame: RealtimeFrame) -> None:
        """向指定节点投递一帧。"""
        ...
