"""RealtimeGateway 的节点会话实现。"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.hasn_im.application.node_session_service import node_session_service
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame


@dataclass(slots=True)
class NodeSessionRealtimeGateway:
    """通过节点会话服务向 owner 或指定节点投递实时帧。"""

    _HASN_ENVELOPE = 'hasn/0.2'

    async def push_to_owner(self, owner_id: str, frame: RealtimeFrame) -> None:
        await node_session_service.push_to_owner(owner_id, self._envelope(frame))

    async def push_to_node(self, node_id: str, frame: RealtimeFrame) -> None:
        await node_session_service.push_to_node(node_id, self._envelope(frame))

    def _envelope(self, frame: RealtimeFrame) -> dict:
        return {'hasn': self._HASN_ENVELOPE, 'method': frame.method, 'params': frame.params}


__all__ = ['NodeSessionRealtimeGateway']
