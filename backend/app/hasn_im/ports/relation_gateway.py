"""hasn_im.ports.relation_gateway · RelationGateway 契约（§9.2）

联系人 REST、MCP、名片建联、自动好友请求和后台管理**只能**调用该 port（§9.2）。
通用 contacts CRUD 必须关闭（R2-08）。关系、信任、拉黑、联系人请求只能经此 port 修改（§0.1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EffectiveRelation:
    """判权用的有效关系（值对象）。"""

    relation_type: str
    trust_level: int
    status: str
    blocked: bool = False
    scope: dict[str, Any] | None = None
    custom_permissions: dict[str, Any] | None = None


@runtime_checkable
class RelationGateway(Protocol):
    """关系域对外唯一写入口。"""

    async def request_contact(
        self,
        *,
        from_hasn_id: str,
        to_hasn_id: str,
        relation_type: str = 'social',
        requested_trust_level: int = 2,
        message: str | None = None,
        channel_source: str | None = None,
    ) -> dict[str, Any]:
        """发起联系人请求（生命周期在 hasn_contact_requests）。"""
        ...

    async def accept_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """通过请求 → 落 hasn_contacts 行（审计链 resulting_contact_id）。"""
        ...

    async def reject_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """拒绝请求。"""
        ...

    async def update_trust(
        self, *, owner_hasn_id: str, peer_hasn_id: str, trust_level: int
    ) -> dict[str, Any]:
        """调整信任等级。"""
        ...

    async def block(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """拉黑（trust_level=0 / status=blocked）。"""
        ...

    async def unblock(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """解除拉黑。"""
        ...

    async def remove_relation(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """删除关系。"""
        ...

    async def resolve_effective_relation(
        self, *, owner_hasn_id: str, peer_hasn_id: str
    ) -> EffectiveRelation | None:
        """解析有效关系（供通信判权，不反向调用交易/服务 API，§9.1）。"""
        ...
