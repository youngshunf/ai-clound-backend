"""ws_node 协议入口的应用运行时。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import WebSocket

from backend.app.hasn_im.application.local_gateway import (
    mark_read as local_mark_read,
    send_to_target,
)
from backend.app.hasn_im.application.node_session_service import node_session_service

_legacy_message_router = SimpleNamespace(route_message=send_to_target, mark_read=local_mark_read)


class WsNodeRuntime:
    """组合节点会话与消息应用服务，供 WS 协议入口调用。"""

    @property
    def message_router(self):
        """供协议层兼容 patch：返回现网 message_router 模块。"""
        return _legacy_message_router

    async def claim_offline_messages(
        self,
        entity_ids: list[str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        return await node_session_service.claim_offline_messages(entity_ids)

    async def ack_offline_messages(self, claims: dict[str, list[str]]) -> None:
        await node_session_service.ack_offline_messages(claims)

    async def get_offline_messages(self, entity_ids: list[str]) -> list[dict]:
        return await node_session_service.get_offline_messages(entity_ids)

    async def push_to_owner(self, owner_id: str, payload: dict[str, Any]) -> bool:
        return await node_session_service.push_to_owner(owner_id, payload)

    async def push_to_owner_excluding_agent_node(
        self,
        owner_id: str,
        agent_id: str,
        payload: dict[str, Any],
    ) -> bool:
        return await node_session_service.push_to_owner_excluding_agent_node(
            owner_id,
            agent_id,
            payload,
        )

    async def broadcast_sync_invalidate(
        self,
        kind: str,
        revision: str,
        owner_id: str | None = None,
    ) -> int:
        return await node_session_service.broadcast_sync_invalidate(
            kind,
            revision,
            owner_id=owner_id,
        )

    async def disconnect_node(self, node_id: str) -> bool:
        return await node_session_service.disconnect_node(node_id)

    async def is_human_online(self, owner_hasn_id: str) -> bool:
        return await node_session_service.is_human_online(owner_hasn_id)

    async def is_agent_online(self, agent_hasn_id: str) -> bool:
        return await node_session_service.is_agent_online(agent_hasn_id)

    async def is_node_online(self, node_id: str) -> bool:
        return await node_session_service.is_node_online(node_id)

    async def get_entity_node(self, hasn_id: str) -> str | None:
        return await node_session_service.get_entity_node(hasn_id)

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        return await node_session_service.get_online_map(entity_ids)

    async def register_node(
        self,
        node_id: str,
        node_type: str,
        ws: WebSocket,
        capacity: int = 1,
    ) -> str:
        return await node_session_service.register_node(
            node_id=node_id,
            node_type=node_type,
            ws=ws,
            capacity=capacity,
        )

    async def mark_node_ready(self, node_id: str, connection_id: str) -> bool:
        return await node_session_service.mark_node_ready(node_id=node_id, connection_id=connection_id)

    async def refresh_node_presence(self, node_id: str, connection_id: str) -> bool:
        return await node_session_service.refresh_node_presence(node_id=node_id, connection_id=connection_id)

    async def unregister_node(self, node_id: str, connection_id: str) -> bool:
        return await node_session_service.unregister_node(node_id=node_id, connection_id=connection_id)

    async def add_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
        *,
        skip_proof_verify: bool = False,
    ) -> dict[str, Any]:
        return await node_session_service.add_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
            skip_proof_verify=skip_proof_verify,
        )

    async def remove_owner(self, node_id: str, owner_id: str, db) -> dict[str, Any]:
        return await node_session_service.remove_owner(node_id=node_id, owner_id=owner_id, db=db)

    async def renew_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
    ) -> dict[str, Any]:
        return await node_session_service.renew_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
        )

    async def list_owners(self, node_id: str) -> dict[str, Any]:
        return await node_session_service.list_owners(node_id=node_id)

    async def add_agent_presence(
        self,
        node_id: str,
        agent_id: str,
        owner_id: str,
        db,
    ) -> dict[str, Any]:
        return await node_session_service.add_agent_presence(
            node_id=node_id,
            agent_id=agent_id,
            owner_id=owner_id,
            db=db,
        )

    async def remove_agent_presence(self, node_id: str, agent_id: str) -> dict[str, Any]:
        return await node_session_service.remove_agent_presence(node_id=node_id, agent_id=agent_id)

    async def set_agent_readiness(self, agent_id: str, online_status: str, health_status: str | None) -> str | None:
        return await node_session_service.set_agent_readiness(agent_id, online_status, health_status)

    async def unregister_entity_route(self, node_id: str, hasn_id: str) -> None:
        await node_session_service.unregister_entity_route(node_id=node_id, hasn_id=hasn_id)

    async def push_message_to(self, to_id: str, payload: dict[str, Any]) -> bool:
        return await node_session_service.push_message_to(to_id, payload)

    async def push_self_sync(self, owner_id: str, payload: dict[str, Any], exclude_node: str) -> None:
        await node_session_service.push_self_sync(owner_id, payload, exclude_node)

    async def route_message(self, **kwargs: Any) -> dict[str, Any]:
        """调用现网 `message_router.route_message`（兼容层）。"""
        return await _legacy_message_router.route_message(**kwargs)

    async def mark_read(self, db, reader: str, conversation_id: str, last_msg_id: int) -> None:
        await _legacy_message_router.mark_read(db, reader, conversation_id, last_msg_id)


ws_node_runtime = WsNodeRuntime()

__all__ = ['ws_node_runtime', 'WsNodeRuntime']
