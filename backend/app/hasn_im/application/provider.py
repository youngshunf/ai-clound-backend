"""hasn_im.application.provider · ImGateway 实例装配（单一构造点）

业务模块（MCP 工具 / API / service）经 `get_im_gateway()` 拿到 `ImGateway` **抽象**，
不直接构造具体实现（§0.1：业务只认 ports 抽象）。第一版返回 `PythonLocalImGateway`
（R1 包装现网 route_message；R2 起替换为独立事务/事件写点的实现，调用方零改动）。

port 自持 `session_factory`（`async_db_session`），每次调用自开事务边界——不向调用方
暴露 Session（§5.2）。构造惰性、无副作用，可安全在请求内重复取用。
"""

from __future__ import annotations

import asyncio

from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.application.node_session_service import node_session_service
from backend.app.hasn_im.ports import ImGateway, NodeSessionGateway, PresenceQuery
from backend.app.hasn_im.ports.presence_query import OnlinePresence
from backend.app.hasn_im.ports.realtime_gateway import RealtimeGateway
from backend.app.hasn_im.adapters.routing.node_session_realtime_gateway import NodeSessionRealtimeGateway
from backend.database.db import async_db_session


class _NodeSessionPresenceQuery(PresenceQuery):
    """基于节点会话服务的 PresenceQuery 实现。"""

    async def is_human_online(self, owner_hasn_id: str) -> bool:
        return await node_session_service.is_human_online(owner_hasn_id)

    async def is_agent_online(self, agent_hasn_id: str) -> bool:
        return await node_session_service.is_agent_online(agent_hasn_id)

    async def is_node_online(self, node_id: str | None) -> bool:
        if not node_id:
            return False
        return await node_session_service.is_node_online(node_id)

    async def get_entity_node(self, hasn_id: str) -> str | None:
        return await node_session_service.get_entity_node(hasn_id)

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        return await node_session_service.get_online_map(entity_ids)

    async def get_online_presence(self, entity_ids: list[str]) -> dict[str, OnlinePresence]:
        if not entity_ids:
            return {}
        online_map = await self.get_online_map(entity_ids)
        nodes = await asyncio.gather(
            *(self.get_entity_node(eid) for eid in entity_ids),
            return_exceptions=True,
        )
        return {
            entity_id: OnlinePresence(
                hasn_id=entity_id,
                is_online=online_map.get(entity_id, False),
                node_id=node_id if isinstance(node_id, str) else None,
            )
            for entity_id, node_id in zip(entity_ids, nodes)
        }


_presence_query_instance: PresenceQuery | None = None
_node_session_gateway_instance: NodeSessionGateway | None = None
_realtime_gateway_instance: RealtimeGateway | None = None


def get_im_gateway() -> ImGateway:
    """取得通信域唯一写/读入口 `ImGateway`（§5.2）。"""
    return PythonLocalImGateway(session_factory=async_db_session)


def get_presence_query() -> PresenceQuery:
    """取得 Presence 查询端口（临时兼容实现）。"""
    global _presence_query_instance
    if _presence_query_instance is None:
        _presence_query_instance = _NodeSessionPresenceQuery()
    return _presence_query_instance


def get_node_session_gateway() -> NodeSessionGateway:
    """取得节点会话与 Presence 管理端口。"""
    global _node_session_gateway_instance
    if _node_session_gateway_instance is None:
        _node_session_gateway_instance = node_session_service
    return _node_session_gateway_instance


def get_realtime_gateway() -> RealtimeGateway:
    """取得实时推送端口。"""
    global _realtime_gateway_instance
    if _realtime_gateway_instance is None:
        _realtime_gateway_instance = NodeSessionRealtimeGateway()
    return _realtime_gateway_instance
