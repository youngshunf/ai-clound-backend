"""hasn_im.ports.presence_query · Presence 查询 port（§2.2 / P1-01）。

该 port 负责 IM 运行时在线态查询，面向业务的唯一读取路径：

- Owner 在线态：`is_human_online`
- Agent 在线态：`is_agent_online`
- node 在线态：`is_node_online`
- 实体所在节点：`get_entity_node`
- 批量在线图：`get_online_map`

约束：
- 不暴露 Redis 连接、WebSocket、数据库 session 或任何投递能力；
- 不直接进行副作用；失败只返回布尔/字符串，不吞掉判权错误；
- 判定语义要求统一到单一实现（后续收口到 `hasn_im.adapters.routing`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OnlinePresence:
    """在线查询返回值。"""

    hasn_id: str
    is_online: bool
    node_id: str | None = None


@runtime_checkable
class PresenceQuery(Protocol):
    """在线态读取端口：统一所有在线查询入口。"""

    async def is_human_online(self, owner_hasn_id: str) -> bool:
        """Owner 是否在线：需满足节点健康与 owner 路由齐全条件。"""
        ...

    async def is_agent_online(self, agent_hasn_id: str) -> bool:
        """Agent 是否在线：需满足节点健康与 agent 运行时就绪双闸。"""
        ...

    async def is_node_online(self, node_id: str | None) -> bool:
        """节点是否在线：以心跳 TTL 为准。"""
        ...

    async def get_entity_node(self, hasn_id: str) -> str | None:
        """查询实体当前归属节点；未在线返回 None。"""
        ...

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        """批量在线图：一次读取，避免 N+1。"""
        ...

    async def get_online_presence(self, entity_ids: list[str]) -> dict[str, OnlinePresence]:
        """批量在线态详情（同样一次性读取，含 node_id）。"""
        ...
