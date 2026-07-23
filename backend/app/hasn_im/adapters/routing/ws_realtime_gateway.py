"""RealtimeGateway 的现网实现，复用现有 ws_router 消息通道。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame


@dataclass(slots=True)
class WsRouterRealtimeGateway:
    """RealtimeGateway 的 ws_router 实现（跨 worker 投递）。"""

    _HASN_ENVELOPE = 'hasn/0.2'

    async def push_to_owner(self, owner_id: str, frame: RealtimeFrame) -> None:
        from backend.app.hasn_im.adapters.routing import ws_router as routing_ws_router

        await routing_ws_router.ws_router.push_to_owner(owner_id, self._envelope(frame))

    async def push_to_node(self, node_id: str, frame: RealtimeFrame) -> None:
        # 现网 ws_router 当前仅提供 owner 级投递。
        # realtime_notifier 本阶段仅推 owner，保留明确提示避免暗中回退到假实现。
        raise NotImplementedError('ws_router 暂无 node 级投递；realtime_notifier 只推 owner')

    def _envelope(self, frame: RealtimeFrame) -> dict:
        return {'hasn': self._HASN_ENVELOPE, 'method': frame.method, 'params': frame.params}


__all__ = ['WsRouterRealtimeGateway']
