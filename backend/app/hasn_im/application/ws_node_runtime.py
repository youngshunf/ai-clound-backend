"""ws_node 协议入口的路由适配层（兼容桥）。

目标：把 WebSocket 协议层从 `backend.app.hasn.service.ws_router` /
`message_router` 的直接 import 脱钩，先通过 adapter 中转，后续再逐步切到
真正的 port-only 运行时。

说明：该模块对调用方只暴露兼容接口，不改变现网行为。
"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import WebSocket

from backend.app.hasn_im.adapters.routing import ws_router as routing_ws_router


class WsNodeRuntime:
    """ws_node 兼容 adapter：当前阶段保留现网路由实现，避免协议层二次封装膨胀。"""

    async def claim_offline_messages(
        self,
        entity_ids: list[str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        return await routing_ws_router.ws_router.claim_offline_messages(entity_ids)

    async def ack_offline_messages(self, claims: dict[str, list[str]]) -> None:
        await routing_ws_router.ws_router.ack_offline_messages(claims)

    async def register_node(
        self,
        node_id: str,
        node_type: str,
        ws: WebSocket,
        capacity: int = 1,
    ) -> str:
        return await routing_ws_router.ws_router.register_node(
            node_id=node_id,
            node_type=node_type,
            ws=ws,
            capacity=capacity,
        )

    async def mark_node_ready(self, node_id: str, connection_id: str) -> bool:
        return await routing_ws_router.ws_router.mark_node_ready(node_id=node_id, connection_id=connection_id)

    async def refresh_node_presence(self, node_id: str, connection_id: str) -> bool:
        return await routing_ws_router.ws_router.refresh_node_presence(node_id=node_id, connection_id=connection_id)

    async def unregister_node(self, node_id: str, connection_id: str) -> bool:
        return await routing_ws_router.ws_router.unregister_node(node_id=node_id, connection_id=connection_id)

    async def add_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
        *,
        skip_proof_verify: bool = False,
    ) -> dict[str, Any]:
        return await routing_ws_router.ws_router.add_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
            skip_proof_verify=skip_proof_verify,
        )

    async def remove_owner(self, node_id: str, owner_id: str, db) -> dict[str, Any]:
        return await routing_ws_router.ws_router.remove_owner(node_id=node_id, owner_id=owner_id, db=db)

    async def renew_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
    ) -> dict[str, Any]:
        return await routing_ws_router.ws_router.renew_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
        )

    async def list_owners(self, node_id: str) -> dict[str, Any]:
        return await routing_ws_router.ws_router.list_owners(node_id=node_id)

    async def add_agent_presence(
        self,
        node_id: str,
        agent_id: str,
        owner_id: str,
        db,
    ) -> dict[str, Any]:
        return await routing_ws_router.ws_router.add_agent_presence(
            node_id=node_id,
            agent_id=agent_id,
            owner_id=owner_id,
            db=db,
        )

    async def remove_agent_presence(self, node_id: str, agent_id: str) -> dict[str, Any]:
        return await routing_ws_router.ws_router.remove_agent_presence(node_id=node_id, agent_id=agent_id)

    async def set_agent_readiness(self, agent_id: str, online_status: str, health_status: str | None) -> str | None:
        return await routing_ws_router.ws_router.set_agent_readiness(agent_id, online_status, health_status)

    async def unregister_entity_route(self, node_id: str, hasn_id: str) -> None:
        await routing_ws_router.ws_router.unregister_entity_route(node_id=node_id, hasn_id=hasn_id)

    async def push_message_to(self, to_id: str, payload: dict[str, Any]) -> bool:
        return await routing_ws_router.ws_router.push_message_to(to_id, payload)

    async def push_self_sync(self, owner_id: str, payload: dict[str, Any], exclude_node: str) -> None:
        await routing_ws_router.ws_router.push_self_sync(owner_id, payload, exclude_node)

    async def route_message(self, **kwargs: Any) -> dict[str, Any]:
        """调用现网 `message_router.route_message`（兼容层）。"""
        message_router = importlib.import_module('backend.app.hasn.service.message_router')
        return await message_router.route_message(**kwargs)

    async def mark_read(self, db, reader: str, conversation_id: str, last_msg_id: int) -> None:
        message_router = importlib.import_module('backend.app.hasn.service.message_router')
        await message_router.mark_read(db, reader, conversation_id, last_msg_id)


ws_node_runtime = WsNodeRuntime()

__all__ = ['ws_node_runtime', 'WsNodeRuntime']
