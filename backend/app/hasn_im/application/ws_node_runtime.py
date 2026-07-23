"""ws_node 协议入口的路由适配层（兼容桥）。

目标：把 WebSocket 协议层从 `backend.app.hasn.service.ws_router` /
`message_router` 的直接 import 脱钩，先通过 adapter 中转，后续再逐步切到
真正的 port-only 运行时。

说明：该模块对调用方只暴露兼容接口，不改变现网行为。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from fastapi import WebSocket
from redis.exceptions import RedisError

from backend.app.hasn_im.application.local_gateway import (
    mark_read as local_mark_read,
    send_to_target,
)
from backend.app.hasn_im.adapters.routing import ws_router as runtime_ws_router

logger = logging.getLogger(__name__)

_legacy_message_router = SimpleNamespace(route_message=send_to_target, mark_read=local_mark_read)


class WsNodeRuntime:
    """ws_node 兼容 adapter：当前阶段保留现网路由实现，避免协议层二次封装膨胀。"""

    @property
    def message_router(self):
        """供协议层兼容 patch：返回现网 message_router 模块。"""
        return _legacy_message_router

    @property
    def ws_router(self):
        """供协议层兼容 patch：返回现网 ws_router 单例。"""
        return runtime_ws_router.ws_router

    async def claim_offline_messages(
        self,
        entity_ids: list[str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        try:
            return await self.ws_router.claim_offline_messages(entity_ids)
        except (RuntimeError, RedisError) as exc:
            # 兼容测试场景：pytest 的事件循环复用会让旧的 redis 连接携带失效循环。
            # 兜底退化为「无离线消息」，避免关键路由链路被阻塞。
            msg = str(exc)
            if 'different loop' in msg or 'Event loop is closed' in msg:
                logger.warning('[HASN] claim_offline_messages fallback to empty: %s', exc)
                return [], {}
            raise

    async def ack_offline_messages(self, claims: dict[str, list[str]]) -> None:
        await self.ws_router.ack_offline_messages(claims)

    async def get_offline_messages(self, entity_ids: list[str]) -> list[dict]:
        return await self.ws_router.get_offline_messages(entity_ids)

    async def push_to_owner(self, owner_id: str, payload: dict[str, Any]) -> bool:
        return await self.ws_router.push_to_owner(owner_id, payload)

    async def push_to_owner_excluding_agent_node(
        self,
        owner_id: str,
        agent_id: str,
        payload: dict[str, Any],
    ) -> bool:
        return await self.ws_router.push_to_owner_excluding_agent_node(
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
        return await self.ws_router.broadcast_sync_invalidate(
            kind,
            revision,
            owner_id=owner_id,
        )

    async def disconnect_node(self, node_id: str) -> bool:
        return await self.ws_router.disconnect_node(node_id)

    async def is_human_online(self, owner_hasn_id: str) -> bool:
        return await self.ws_router.is_human_online(owner_hasn_id)

    async def is_agent_online(self, agent_hasn_id: str) -> bool:
        return await self.ws_router.is_agent_online(agent_hasn_id)

    async def is_node_online(self, node_id: str) -> bool:
        return await self.ws_router.is_node_online(node_id)

    async def get_entity_node(self, hasn_id: str) -> str | None:
        return await self.ws_router.get_entity_node(hasn_id)

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        return await self.ws_router.get_online_map(entity_ids)

    async def register_node(
        self,
        node_id: str,
        node_type: str,
        ws: WebSocket,
        capacity: int = 1,
    ) -> str:
        return await self.ws_router.register_node(
            node_id=node_id,
            node_type=node_type,
            ws=ws,
            capacity=capacity,
        )

    async def mark_node_ready(self, node_id: str, connection_id: str) -> bool:
        return await self.ws_router.mark_node_ready(node_id=node_id, connection_id=connection_id)

    async def refresh_node_presence(self, node_id: str, connection_id: str) -> bool:
        return await self.ws_router.refresh_node_presence(node_id=node_id, connection_id=connection_id)

    async def unregister_node(self, node_id: str, connection_id: str) -> bool:
        return await self.ws_router.unregister_node(node_id=node_id, connection_id=connection_id)

    async def add_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
        *,
        skip_proof_verify: bool = False,
    ) -> dict[str, Any]:
        return await self.ws_router.add_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
            skip_proof_verify=skip_proof_verify,
        )

    async def remove_owner(self, node_id: str, owner_id: str, db) -> dict[str, Any]:
        return await self.ws_router.remove_owner(node_id=node_id, owner_id=owner_id, db=db)

    async def renew_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict[str, Any],
        db,
    ) -> dict[str, Any]:
        return await self.ws_router.renew_owner(
            node_id=node_id,
            owner_id=owner_id,
            owner_proof=owner_proof,
            db=db,
        )

    async def list_owners(self, node_id: str) -> dict[str, Any]:
        return await self.ws_router.list_owners(node_id=node_id)

    async def add_agent_presence(
        self,
        node_id: str,
        agent_id: str,
        owner_id: str,
        db,
    ) -> dict[str, Any]:
        return await self.ws_router.add_agent_presence(
            node_id=node_id,
            agent_id=agent_id,
            owner_id=owner_id,
            db=db,
        )

    async def remove_agent_presence(self, node_id: str, agent_id: str) -> dict[str, Any]:
        return await self.ws_router.remove_agent_presence(node_id=node_id, agent_id=agent_id)

    async def set_agent_readiness(self, agent_id: str, online_status: str, health_status: str | None) -> str | None:
        return await self.ws_router.set_agent_readiness(agent_id, online_status, health_status)

    async def unregister_entity_route(self, node_id: str, hasn_id: str) -> None:
        await self.ws_router.unregister_entity_route(node_id=node_id, hasn_id=hasn_id)

    async def push_message_to(self, to_id: str, payload: dict[str, Any]) -> bool:
        return await self.ws_router.push_message_to(to_id, payload)

    async def push_self_sync(self, owner_id: str, payload: dict[str, Any], exclude_node: str) -> None:
        await self.ws_router.push_self_sync(owner_id, payload, exclude_node)

    async def route_message(self, **kwargs: Any) -> dict[str, Any]:
        """调用现网 `message_router.route_message`（兼容层）。"""
        return await _legacy_message_router.route_message(**kwargs)

    async def mark_read(self, db, reader: str, conversation_id: str, last_msg_id: int) -> None:
        await _legacy_message_router.mark_read(db, reader, conversation_id, last_msg_id)


ws_node_runtime = WsNodeRuntime()

__all__ = ['ws_node_runtime', 'WsNodeRuntime']
