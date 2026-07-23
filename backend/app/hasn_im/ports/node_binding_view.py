"""hasn_im.ports.node_binding_view · Owner 绑定只读视图（§2.2 / P1-01）。

身份域与节点绑定仍归身份域管理。该 port 只做只读读模型投影，
业务方不得直接依赖旧的 `hasn_node_bindings_service` 或数据库 session。

Fail-closed:
- 绑定缺失/过期/状态异常 → 返回 None；
- 未通过调用方的后续判权前置，不应抛出绑定不存在之外的宽松 fallback。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OwnerBindingRef:
    """Owner binding 只读投影（供 NodeSessionGateway 做 fail-closed 基础判定）。"""

    node_id: str
    owner_id: str
    binding_id: str
    status: str
    expires_at: datetime | None


@runtime_checkable
class NodeBindingView(Protocol):
    """Owner 绑定只读视图。"""

    async def get_active_owner_binding(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingRef | None:
        """查某 node-owner 的 active 且未过期绑定；无绑定返回 None。"""
        ...

    async def list_active_owner_bindings(self, *, node_id: str) -> list[OwnerBindingRef]:
        """查询某 node 的全部 active 绑定（在线广播和权限审计用）。"""
        ...
