"""hasn_im.ports.relation_gateway · RelationGateway 契约（§9.2）

联系人 REST、MCP、名片建联、自动好友请求和后台管理**只能**调用该 port（§9.2）。
通用 contacts CRUD 必须关闭（R2-08）。关系、信任、拉黑、联系人请求只能经此 port 修改（§0.1）。

**实现分期（务必先读，勿据此建 R1 假 wrapper）**：本 port 是 **R2-08「关系域收编」的目标态契约**，
**不是** R1 可薄封装的接缝。与 `ImGateway`/`SyncAppender` 不同——那两者现网各有**单一已收敛实现**
可原样封装；关系写路径当前**散落在 API 层**（`api/v1/app/contacts.py` 的 respond_to_request /
update_trust_level 把 DAO 调用、互建边、推送事件混在一起），且现网 `HasnContactsService.request_contact`
语义更窄（`requester_hasn_id`/`target` 唤星号解析、social-only、trust 派生），与本 port 的
`from_hasn_id`/`to_hasn_id` + `relation_type`/`requested_trust_level` **语义**不一致（非仅命名差异）。
强行在 R1 造 wrapper 去桥接会做成行为不忠实的假实现（违反零 fake）。故：
- **R1-03 的真实产出 = 关系写调用点清单（R0-02）+ 本契约冻结**，供 R2-08「逐个可切」，**不含** wrapper。
- **真实现在 R2-08**：`permission_engine`/`inbound_gatekeeper` 内化进 `hasn_im/domain`，散落的 API 编排
  忠实迁进本 port 的实现体，通用 contacts 写 CRUD 关闭。届时本 port 形状不变、只落实现。
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

    async def ensure_owner_agent_control_edge(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
    ) -> dict[str, Any]:
        """把已提交的 Agent 身份事实幂等投影为 owner→agent 控制边。"""
        ...

    async def sweep_expired_relation_lifecycle(self) -> dict[str, int]:
        """收敛过期联系人请求和到期关系。"""
        ...

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

    async def withdraw_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """发起方撤回仍为 pending 的请求。"""
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

    async def update_permissions(
        self,
        *,
        owner_hasn_id: str,
        peer_hasn_id: str,
        permissions: dict[str, str],
    ) -> dict[str, Any]:
        """合并自定义权限覆盖，并执行协议铁律校验。"""
        ...

    async def resolve_effective_relation(
        self, *, owner_hasn_id: str, peer_hasn_id: str
    ) -> EffectiveRelation | None:
        """解析有效关系（供通信判权，不反向调用交易/服务 API，§9.1）。"""
        ...

    async def materialize_derived_agent(
        self,
        *,
        owner_hasn_id: str,
        peer_agent_hasn_id: str,
        peer_owner_hasn_id: str,
        trust_level: int,
    ) -> dict[str, Any]:
        """把主人关系派生为 owner→对方分身的通信边。"""
        ...

    async def ensure_auto_first_contact_request(
        self,
        *,
        from_agent_hasn_id: str,
        receiver_hasn_id: str,
        receiver_owner_hasn_id: str,
        receiver_type: str,
    ) -> int:
        """幂等创建自动首联请求并返回权威请求 ID。"""
        ...

    async def upsert_release_contact(
        self,
        *,
        owner_hasn_id: str,
        peer_hasn_id: str,
        minimum_trust_level: int = 2,
        request_id: int | None = None,
    ) -> dict[str, Any]:
        """主人放行时幂等建边/提档，并可原子接受关联的自动首联请求。"""
        ...

    async def update_agent_communication_settings(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        social_enabled: bool | None = None,
        inbound_policy: str | None = None,
    ) -> dict[str, Any]:
        """主人更新自有分身的 IM 通信设置。"""
        ...

    async def get_agent_communication_settings(
        self,
        *,
        agent_hasn_id: str,
    ) -> dict[str, Any]:
        """读取分身的 IM 权威通信设置；未显式建行时返回协议默认值。"""
        ...

    async def filter_socially_enabled_agents(
        self,
        *,
        agent_hasn_ids: list[str],
    ) -> set[str]:
        """从候选分身中返回允许公开社交发现的 ID。"""
        ...
