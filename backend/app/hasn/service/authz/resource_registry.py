"""ResourceKindRegistry：G6 门的资源类型适配器注册表（doc32 §4）。

平台层（`authz/`）唯一需要各应用「贡献」的东西：每个有实例级 ACL 的资源类型注册一个
`ResourceKindAdapter`，告诉门「这个类型的资源怎么按权威 id 取元信息、有没有父链、有没有
维度②域限制」。判权逻辑、错误语义、审计、继承规则全在平台层（`resource_gate.py`），
应用侧只写「取行」这几行（doc32 §4.3 全文示例）。

层次纪律（doc32 §3）：本模块属平台层，**禁止 import 任何 `app/hasn_*` 应用模块**——
adapter 由各应用在自己的 platform 注册模块里 `resource_kind_registry.register(...)`，
依赖方向永远是「应用 → 平台」，不反转。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ResourceMeta:
    """G6 判权所需的资源元信息（与 `resolve_effective_permission` 入参一一对应，doc32 §4.1）。"""

    resource_id: str  # 权威 id（str 化）
    owner_hasn_id: str  # 资源 owner 的 hasn_id
    owner_scope: str  # personal | enterprise
    enterprise_id: int | None
    visibility: str  # private | enterprise | link
    # 原始 ORM 行——**只读快照**（doc32 §4.1 评审修订）：MCP 面判权 session 与 handler session
    # 不同（行是 detached 的），只保证已加载标量属性可读；禁止 lazy load / 拿去 update——
    # 写路径 handler 仍按 id 在自己的 session 取行。
    row: Any


@runtime_checkable
class ResourceKindAdapter(Protocol):
    """资源类型适配器契约（doc32 §4.1）。

    必选成员：`resource_type` / `id_param_aliases` / `load_meta`。
    可选成员（门用 `getattr` 探测，未实现视为缺省）：
    - `has_own_shares: bool`——子资源自身也有独立 share 行时置 True（如 knowledge_doc）；
      门取 `max(自身档位, 父档位)`。缺省 False：自身恒 none，取 max 等价纯父链，可省一次自身查询。
    - `async resolve_parent(db, resource_id) -> tuple[父type, 父id] | None`——子资源父链。
      父 id 从**子行**读出（子行不存在时门已 404，不上溯，doc32 §5.3 评审修订）。
    - `async agent_domain_grant(db, owner_id, agent_hasn_id) -> tuple[mode, 白名单id列表]`——
      维度②域限制钩子（doc32 §7.4 一期承载点），未实现视为 ('inherit', [])。
    """

    resource_type: str  # 与 hasn_resource_share.resource_type、manifest resources[] 同名同串
    # 本类型资源 id 在工具入参里的惯用参数名（如 ('kb_id',)）；守卫 1（doc32 §9）据此机械匹配
    # 「有 id 参数却无声明」，不靠猜参数名。
    id_param_aliases: tuple[str, ...]

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        """按权威 id 取未删行的元信息；不存在/已删/**id 畸形解析不动**（如非数字串）一律返回 None
        （门统一按存在性隐藏回 404）。入参 id 伪造正是 G6 本职——绝不允许 ValueError 冒 500。"""
        ...


class ResourceKindRegistry:
    """资源类型 → adapter 的全局注册表。进程级单例（`resource_kind_registry`）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ResourceKindAdapter] = {}

    def register(self, adapter: ResourceKindAdapter) -> None:
        """注册一个 adapter；重名即抛，防两个应用/两次导入抢注同一类型（fail-fast）。"""
        rtype = adapter.resource_type
        if not rtype:
            raise ValueError('ResourceKindAdapter.resource_type 不能为空')
        if rtype in self._adapters:
            raise ValueError(f'资源类型 {rtype!r} 已注册 adapter，禁止重复注册')
        self._adapters[rtype] = adapter

    def get(self, resource_type: str) -> ResourceKindAdapter | None:
        """取 adapter；未注册返回 None（门据此抛 500 配置错误，守卫测试应已拦）。"""
        return self._adapters.get(resource_type)

    def registered_types(self) -> frozenset[str]:
        """已注册类型全集（守卫测试与 share 建行运行时校验用，doc32 §9）。"""
        return frozenset(self._adapters)


# 进程级单例：各应用在自己的 platform 注册模块里 import 它并 register(adapter)。
resource_kind_registry = ResourceKindRegistry()
