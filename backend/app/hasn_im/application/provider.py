"""hasn_im.application.provider · ImGateway 实例装配（单一构造点）

业务模块（MCP 工具 / API / service）经 `get_im_gateway()` 拿到 `ImGateway` **抽象**，
不直接构造具体实现（§0.1：业务只认 ports 抽象）。第一版返回 `PythonLocalImGateway`
（R1 包装现网 route_message；R2 起替换为独立事务/事件写点的实现，调用方零改动）。

port 自持 `session_factory`（`im_service_db_session`），每次调用自开事务边界——不向调用方
暴露 Session（§5.2）。构造惰性、无副作用，可安全在请求内重复取用。
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.application.node_session_service import NodeSessionService, node_session_service
from backend.app.hasn_im.adapters.sqlalchemy_relation_gateway import SqlAlchemyRelationGateway
from backend.app.hasn_im.ports import ImGateway, PresenceQuery, RelationGateway
from backend.app.hasn_im.ports.presence_query import OnlinePresence
from backend.app.hasn_im.ports.realtime_gateway import RealtimeGateway
from backend.app.hasn_im.adapters.routing.node_session_realtime_gateway import NodeSessionRealtimeGateway
from backend.database.db import im_service_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
_node_session_gateway_instance: NodeSessionService | None = None
_realtime_gateway_instance: RealtimeGateway | None = None


def get_im_gateway() -> ImGateway:
    """取得通信域唯一写/读入口 `ImGateway`（§5.2）。"""
    return PythonLocalImGateway(session_factory=im_service_db_session)


def get_transactional_im_gateway(db: AsyncSession) -> ImGateway:
    """取得仅将 ensure 绑定到当前业务事务的 IM 网关。

    通知汇报卡需要让会话/membership 与生产方 outbox 原子出现，并读取同事务中新建的
    身份；实际消息投递仍由 relay 通过普通自管事务网关完成。
    """
    return PythonLocalImGateway(
        session_factory=im_service_db_session,
        bound_ensure_session=db,
    )


def get_relation_gateway() -> RelationGateway:
    """取得关系域唯一写入口，并固定使用 IM 受限角色连接池。"""
    return SqlAlchemyRelationGateway(session_factory=im_service_db_session)


def get_transactional_relation_gateway(db: AsyncSession) -> RelationGateway:
    """取得绑定当前 IM 事务的关系入口，不新开会话、不提前提交。"""
    return SqlAlchemyRelationGateway(bound_session=db)


def get_presence_query() -> PresenceQuery:
    """取得 Presence 查询端口（临时兼容实现）。"""
    global _presence_query_instance
    if _presence_query_instance is None:
        _presence_query_instance = _NodeSessionPresenceQuery()
    return _presence_query_instance


def get_node_session_gateway() -> NodeSessionService:
    """取得节点会话应用服务的默认实现。"""
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
