"""hasn_im.ports.node_session_gateway · WS 节点会话与实体在线路由写 port（§2.2 / P1-01）。

所有业务对节点会话、Owner/Agent 上线、节点生命周期与下线只允许走该 port；
禁止直接持有 WebSocket、Redis client、SQL session 或旧的业务 service。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class NodeRegistration:
    """节点注册返回值。"""

    node_id: str
    connection_id: str
    node_type: str
    capacity: int


@dataclass(frozen=True, slots=True)
class NodeSessionResult:
    """通用接入结果。"""

    accepted: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class OwnerBindingResult(NodeSessionResult):
    """Owner 绑定操作结果。"""

    binding_id: str | None = None
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSessionResult(NodeSessionResult):
    """Agent 路由操作结果。"""

    agent_id: str | None = None
    entity_node: str | None = None


@runtime_checkable
class NodeSessionGateway(Protocol):
    """节点会话与 Presence 注册的命令口。"""

    async def register_node(
        self,
        *,
        node_id: str,
        node_type: str,
        capacity: int = 1,
    ) -> NodeRegistration:
        """注册/更新节点代际，返回新 connection_id。"""
        ...

    async def mark_node_ready(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        """仅当前代际可就绪；返回是否生效。"""
        ...

    async def refresh_node_presence(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        """代际一致才可续期；返回是否续期成功。"""
        ...

    async def unregister_node(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        """注销节点共享 presence 与路由；返回是否成功清理。"""
        ...

    async def add_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingResult:
        """添加 owner 在线绑定（含绑定前置和幂等）。"""
        ...

    async def renew_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingResult:
        """续期 owner 绑定。"""
        ...

    async def remove_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> NodeSessionResult:
        """移除 owner binding 与其实体路由。"""
        ...

    async def list_owners(self, *, node_id: str) -> list[str]:
        """列出当前节点的在线 owner（用于管理页与清理）。"""
        ...

    async def add_agent_presence(
        self,
        *,
        node_id: str,
        owner_id: str,
        agent_id: str,
    ) -> AgentSessionResult:
        """添加 agent 运行态与路由关联。"""
        ...

    async def remove_agent_presence(
        self,
        *,
        node_id: str,
        agent_id: str,
    ) -> NodeSessionResult:
        """移除 agent 路由并清 readiness（若有）。"""
        ...

    async def set_agent_readiness(
        self,
        *,
        agent_id: str,
        online_status: str,
        health_status: str | None,
    ) -> str | None:
        """设置 agent readiness：返回是否发生 online/offline 翻转。"""
        ...

    async def unregister_entity_route(
        self,
        *,
        node_id: str,
        hasn_id: str,
    ) -> None:
        """移除单实体路由。"""
        ...

    async def disconnect_node(
        self,
        *,
        node_id: str,
    ) -> bool:
        """跨 worker 安全断连：清 shared presence；返回是否清理到本进程持有连接。"""
        ...
