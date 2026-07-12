"""资产投影门（Asset Projection Gate·doc32 §14）：分享资源内嵌私有资产的随行授权。

G6 registry 的**第二消费者**，与 G6 资源实例门（`resource_gate`）正交互补、判权内核同源：
- G6 门判「分身/主人**动**资源实例」（工具 / HTTP 面，viewer/editor/manager）；
- 本门判「被分享者**看**资源内嵌的私有资产」（资产解析边界 `POST /assets/resolve`，固定 viewer）。

一个资源被分享出去后，被分享者渲染 / 导出它时，其内嵌私有资产（deck 页 `<img
src="hasn://asset/{id}">`、封面、每页缩略图）要按**该资源的 ACL**（≥viewer）才能签出——这是资产
解析边界的「读」授权，G6 未覆盖。本门在同一套 `ResourceKindRegistry` + `resolve_effective_permission`
上补这一刀：adapter 多一个 `collect_asset_ids` 钩子声明「这个资源引用了哪些资产」，本门据资源 ACL
把「**该资源确实引用的**资产」纳入可签集。应用接入成本 = 一个 adapter 方法。

层次纪律（doc32 §3）：本模块属平台层，**禁止 import 任何 `app/hasn_*` 应用模块**——资产收集经
adapter 钩子回调，依赖方向恒为「应用→平台」，不反转。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.hasn.service.authz import resource_gate
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn.service.resource_share_service import rank

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.service.authz.subject import Subject


async def readable_asset_ids(
    db: AsyncSession,
    subject: Subject,
    resource_ref: str | None,
    requested_ids: set[str],
) -> set[str]:
    """据 `resource_ref` 的资源 ACL，从 `requested_ids` 里挑出「该主体有权签发」的资产 id 子集。

    产出恒 = `requested_ids ∩ 该资源引用的资产全集`——**交集是防越权不变量，不是优化**（doc32 §14.4）：
    即便持一个**合法** `resource_ref`，也只能签「这个资源确实引用的」资产，不能借它签任意 asset id。

    - `resource_ref` 空 / 语法非 `{resource_type}:{server_id}` / 类型未注册 → 空集（无从判权）；
    - adapter 未实现 `collect_asset_ids`（纯文本等无内嵌资产的类型）→ 空集（门对它自然 no-op，§14.3）；
    - 有效档 < viewer（无权 / 资源不存在 none）→ 空集（存在性隐藏，与 G6 的 404 隐身语义一致，§14.7）。

    判权走 `resource_gate.effective_permission`（load_meta → 父链取并 §5.3 → 维度② §7.4），need 固定
    viewer——owner_grant / admin_grant / visibility / explicit_grant 四层 + 父链 + 维度②天然继承，
    与 G6 逐字一致。
    """
    if not resource_ref:
        return set()
    # 规范语法 {resource_type}:{server_id}（server_id 为云端权威 id，遵「本地 ID 永不上引用」铁律）
    rtype, sep, rid = resource_ref.partition(':')
    if not sep or not rtype or not rid:
        return set()
    adapter = resource_kind_registry.get(rtype)
    if adapter is None:
        return set()
    # 可选钩子探测（与 resolve_parent / agent_domain_grant 同规格）：未实现即该类型无内嵌私有资产
    collect = getattr(adapter, 'collect_asset_ids', None)
    if collect is None:
        return set()
    eff = await resource_gate.effective_permission(db, subject, rtype, rid)
    if rank(eff) < rank('viewer'):
        return set()
    owned = await collect(db, rid)
    return requested_ids & owned
