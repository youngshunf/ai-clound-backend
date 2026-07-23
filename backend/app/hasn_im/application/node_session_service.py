"""IM 节点会话应用服务。

应用层提供节点生命周期、Owner/Agent Presence 与实时投递的单一入口；Redis、
进程内连接和跨 worker 投递细节都收敛在 routing adapter。
"""

from backend.app.hasn_im.adapters.routing.node_session_service import (
    NodeSessionService as RoutingNodeSessionService,
)


class NodeSessionService(RoutingNodeSessionService):
    """节点会话应用服务的默认实现。"""


node_session_service = NodeSessionService()

__all__ = ['NodeSessionService', 'node_session_service']
