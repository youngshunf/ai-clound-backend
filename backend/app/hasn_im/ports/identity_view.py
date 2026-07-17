"""hasn_im.ports.identity_view · 身份只读视图契约（§9.3 阶段一·R2-09）

IM 热路径需要的**最小身份信息**（存在性 + 存活态 + 类型 + 主人）由本 port 提供。设计事实源
16 号 §9.3「身份依赖」：

- **R1-R4（同进程同库）**：认证 claims + `astra_im_service` 被授权的**身份只读视图**。同库视图不是
  出站 RPC，**fail-closed 语义不变——身份行缺失/停用即拒绝新消息**。
- **本阶段不建设身份事件管线**（`identity.changed.v1` + 投影表 + lag SLO 是同进程同库场景不必要的
  管线，评审裁剪、后置到进程抽取 / Rust 后）。

约束：
- IM **只读**身份，绝不写身份表（身份生命周期属身份域）。
- 身份查询**不得**成为每条消息必经的同步上游 RPC（§9.3-3）；本 port 是同库授权只读投影，在**新消息
  发送前置**调用一次做 fail-closed 判定，非每条消息的出站 RPC。
- port 只暴露 `hasn_id / kind / active / owner_id` 四字段，**不含**业务字段——身份域其余信息 IM 不消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# 身份类型：human（人类主人）/ agent（AI 分身）。群 / 系统主体不是「身份」，不经本 port。
IdentityKind = Literal['human', 'agent']


@dataclass(frozen=True, slots=True)
class IdentityRef:
    """IM 热路径所需的最小身份投影（§9.3 授权只读视图产出的值对象）。

    - `hasn_id`：云端权威身份 ID（human `h_{uuid}` / agent `a_{uuid}`）；
    - `kind`：human / agent；
    - `active`：是否存活（human `status=='active'` / agent `status=='active'`；其余生命周期态均为停用）；
    - `owner_id`：human 自身即主人（= 自己的 hasn_id）；agent 为其主人 human 的 hasn_id。
    """

    hasn_id: str
    kind: IdentityKind
    active: bool
    owner_id: str


class IdentityRejected(Exception):
    """身份不满足新消息发送前置——fail-closed 拒绝（§9.3）。

    两种触发：身份行**缺失**（对端 / 发送方在身份域根本不存在）、身份**已停用**
    （human suspended/deleted、agent disabled/revoked/archived/deleted）。
    """


@runtime_checkable
class IdentityView(Protocol):
    """身份只读视图 port（§9.3 阶段一）。同库授权只读投影，fail-closed：

    - `resolve(hasn_id)` 命中存活身份 → `IdentityRef(active=True)`；
    - 命中但已停用 → `IdentityRef(active=False)`；
    - 未命中（身份行缺失）→ `None`。

    实现方**只读** `hasn_humans` / `hasn_agents`，绝不写身份表。
    """

    async def resolve(self, hasn_id: str) -> IdentityRef | None: ...


async def require_active(view: IdentityView, hasn_id: str) -> IdentityRef:
    """fail-closed 发送前置：解析身份，缺失或停用即拒（§9.3）。命中存活则返回其 `IdentityRef`。

    纯前置校验，可作用于任意 `IdentityView` 实现（真库视图 / 测试替身）——发送用例在
    `astra_im_service` 事务内对**发送方**（必要时对端）各调一次。
    """
    ref = await view.resolve(hasn_id)
    if ref is None:
        raise IdentityRejected(f'身份不存在，拒绝新消息：{hasn_id}')
    if not ref.active:
        raise IdentityRejected(f'身份已停用，拒绝新消息：{hasn_id}')
    return ref
