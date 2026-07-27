"""designsystem 应用的 G6 资源类型适配器（doc32 §4·doc33 S3-1）。

设计系统的 service 层本就有 `_assert_can_read` / save 的 `resolve_effective_permission` 判权
（human 面与分身面共用同一内核），G6 在统一派发管线里多判一次是**防御纵深**（代价一次 SELECT）：
分身工具面（`app/mcp/tools/designsystem.py` 的 get/save）声明 `resource_access` 后，门在 ask 审批前
先按同一 `resolve_effective_permission` 内核判权，确定性无权先拒、不打扰主人审批。

单类资源 `designsystem`：leaf id = design_system_id，自身即 share 主体（无父链、无维度②白名单）。

**可见性映射（关键·不动语义）**：`DesignSystem` 表无 `owner_scope`/`visibility` 列，可见语义靠
`is_builtin` + `enterprise_id` 表达（见 service `_readable_fast`：builtin 跨 owner 只读 / 同企业可读 /
owner 自有）。为让平台内核 `resolve_effective_permission`（只认 owner_scope/visibility/enterprise_id）
判出与 service **一致**的档位，`load_meta` 把行状态翻译成内核可读的可见性：
- `is_builtin` → `visibility='link'`：内核对 link 授基线 viewer（复刻「builtin 跨 owner 只读可见」）；
- 非 builtin 且 `enterprise_id` 非空 → `owner_scope='enterprise'` + `visibility='enterprise'`：
  内核对「同企业成员 + enterprise 可见」授 viewer（复刻 `_readable_fast` 同企业可读）；
- 否则 → `owner_scope='personal'` + `visibility='private'`：仅 owner_grant / 显式 share 生效。
owner 本人（分身代主人）恒得 owner_grant=manager；修改 builtin 由 service 先行 ForbiddenError 兜底，
门对非 owner 的 builtin 只判出 viewer < editor → 403，两面同为拒，语义一致。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_designsystem.model import DesignSystem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _to_int(resource_id: str) -> int | None:
    """把权威 id 串转 int；畸形返回 None（门据此 404，绝不冒 500）。"""
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


class DesignSystemResourceAdapter:
    """设计系统资源适配器：resource_type='designsystem'，leaf id = design_system_id。"""

    resource_type: str = 'designsystem'
    id_param_aliases: tuple[str, ...] = ('design_system_id',)

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        ds_id = _to_int(resource_id)
        if ds_id is None:
            return None
        ds = (
            await db.execute(
                sa.select(DesignSystem).where(DesignSystem.id == ds_id, DesignSystem.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if ds is None:
            return None
        # 见模块 docstring「可见性映射」：把 builtin / 企业归属翻译成内核可读的 owner_scope+visibility，
        # 使门判出的档位与 service `_readable_fast` 一致（不动语义）。
        if ds.is_builtin:
            owner_scope, visibility = 'personal', 'link'
        elif ds.enterprise_id is not None:
            owner_scope, visibility = 'enterprise', 'enterprise'
        else:
            owner_scope, visibility = 'personal', 'private'
        return ResourceMeta(
            resource_id=str(ds.id),
            owner_hasn_id=ds.owner_hasn_id,
            owner_scope=owner_scope,
            enterprise_id=ds.enterprise_id,
            visibility=visibility,
            row=ds,
        )


def register() -> None:
    """把 designsystem 资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    adapter = DesignSystemResourceAdapter()
    if adapter.resource_type not in resource_kind_registry.registered_types():
        resource_kind_registry.register(adapter)


# 模块导入即注册（模块缓存保证进程内只跑一次）。平台启动经 ai_native_app_registry 触发；
# designsystem 平台工具模块 import 本模块同样触发，保证门用到 adapter 时它已在注册表。
register()
