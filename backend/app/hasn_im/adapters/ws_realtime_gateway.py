"""hasn_im.adapters.ws_realtime_gateway · RealtimeGateway 的 ws_router 实现（§7.3-2）

薄封装现网 ``ws_router.push_to_owner`` / ``push_to_node``——把 ``RealtimeFrame`` 装进
hasn 协议帧 ``{'hasn': 'hasn/0.2', 'method', 'params'}`` 后经现网跨 worker delivery bus 投递。
best-effort：投递失败由消费者框架记 metric 后仍推进 cursor（§7.2），本 adapter 不吞异常
（抛给框架统一按 best-effort 处理），也不重试。

依赖方向（§0.1）：adapter 层**允许**依赖现网 ws_router（收编期过渡）；消费者只认
``RealtimeGateway`` 抽象，不直接 import 本 adapter。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame

# hasn 协议帧固定信封版本（与 _fanout_message_new 的推送 payload 一致）
_HASN_ENVELOPE = 'hasn/0.2'


@dataclass(slots=True)
class WsRouterRealtimeGateway:
    """RealtimeGateway 的现网实现（经 ws_router 跨 worker 投递）。"""

    async def push_to_owner(self, owner_id: str, frame: RealtimeFrame) -> None:
        from backend.app.hasn.service.ws_router import ws_router

        await ws_router.push_to_owner(owner_id, self._envelope(frame))

    async def push_to_node(self, node_id: str, frame: RealtimeFrame) -> None:
        # 现网 ws_router 只有 owner 级投递总线，无 node 级；realtime_notifier 只推 owner，
        # 本方法暂无调用方。待有 node 级投递需求（如定向单设备）再补现网能力，不造假实现。
        raise NotImplementedError('ws_router 暂无 node 级投递；realtime_notifier 只推 owner')

    @staticmethod
    def _envelope(frame: RealtimeFrame) -> dict:
        return {'hasn': _HASN_ENVELOPE, 'method': frame.method, 'params': frame.params}
