"""design（OpenPencil 矢量设计）应用的 G6 资源类型适配器（doc32 §4·doc33 S3-1）。

design 与 deck/designsystem/knowledge 有一处**本质区别**：它是 **daemon 本地优先权威**——
`design_share.py` 明载「**design 项目是 daemon 权威（project_id = ULID 字符串，云端不持有项目行）**」。
云端 `hasn_design_project` 表只是一张 integer 主键的「轻登记元数据」，**并非按 daemon `project_id`
（工具/分享里用的那个串）可查的项目行**；工具面（manifest `capabilities` 的 `hasn.design.*`）与分享面
（`design_share.py`，`resource_type='design'`、`resource_id=project_id`）用的都是这个**不透明字符串**
`project_id`（daemon 侧 = 一个 .op 文档 id）。

因此本 adapter 的 `load_meta` **不能**像 deck/designsystem 那样按 id 去查一张项目行——云端根本没有。
资源 owner 的**唯一云端事实源 = 分享登记表 `hasn_resource_share`**：owner 分享项目时经
`ResourceShareService.upsert_share` 写入一行，`owner_hasn_id` = 项目主人。故 `load_meta` 取该项目
任一 active 分享行的 `owner_hasn_id` 作资源 owner；**从未进入云端分享登记**的项目（无任何 active 行）→
云端无从判归属 → 返回 None（门按存在性隐藏回 404）。

**可见性映射（不动语义）**：design 的云端分享是**显式 grant 制**（`design_share.py` 只落 grantee 授权，
不走 link/enterprise 可见档），故 `owner_scope='personal'` / `visibility='private'`——仅 owner_grant
（分身代主人）+ 显式 share 生效，与 `design_share.py` 泛型 ACL 语义逐字一致。项目 `public` 的对外查看
走 publish_service（Site /s/{slug}），非工具级判权，故此处不映射 link 可见。

**id 类型（关键·与任务硬约束呼应）**：design `project_id` 是**不透明字符串**（ULID/opaque），**不做**
deck/designsystem 那种 `_to_int` 转换——直接按串比对分享登记表。任何字符串都可安全查询，无 active 行即
None（门 404），**绝不冒 500**（无解析异常面）。

**运行时说明（诚实标注）**：`hasn.design.*` 工具是 `transport_mode='local'` / `execution_mode='local_tool'`
的本地工具（hasn-mcp `crates/hasn-mcp/src/design.rs`，`source=Local`），**不经云端 Runtime Gateway
`_dispatch_tool`**，故云端门 `enforce_declaration` 运行时不会被这些工具触发；本 adapter + manifest
`resource_access` 声明是**声明契约 + 防御纵深**（daemon 侧据 `resource_access` 本地判权；云端门若未来
design 改走云端派发亦可直接复用），并由 per-app 门测试锁死其判权正确性。

**`id_param_aliases` 为何留空（`()`）**：守卫 1（`test_resource_access_declaration_contract`）据别名集
按参数名**机械匹配**「工具入参命中别名却漏声明」。而 design 的 `project_id` 是**跨应用重名参**
（creator / studio / film / imagelab 等也用同名参却各指不同资源），把它做别名会令守卫误伤那些天然
豁免的应用工具（判成漏声明）。运行时门**不用别名**——只据声明里的 `param` + `type` 定位（见
`enforce_declaration`），故留空别名**不影响** design 声明的判权（manifest `resource_access` 逐个判）。
design 工具面声明完整度由 manifest 校验 + 本应用 per-app 门测试锁死（与 studio / plan 同源，doc33 S3-1）。

层次纪律（doc32 §3）：适配器属**应用层**，可 import 平台 `hasn_resource_share` 模型（依赖方向
「应用 → 平台」，允许）；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model import HasnResourceShare
from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 与 design_share.py 的 RESOURCE_TYPE_DESIGN 逐字一致（分享登记里存的 resource_type 串）。
_RESOURCE_TYPE_DESIGN = 'design'


class DesignProjectResourceAdapter:
    """矢量设计项目资源适配器：resource_type='design'，leaf id = project_id（daemon 不透明字符串）。

    与 deck/designsystem 的按行加载不同：design 云端无项目行，owner 从 `hasn_resource_share` 登记推导。
    """

    resource_type = _RESOURCE_TYPE_DESIGN
    # 别名留空：`project_id` 是跨应用重名参（见模块 docstring「为何留空」），做别名会误伤天然豁免的
    # creator/studio/film/imagelab 等同名参工具。运行时门据声明 param+type 判权，不用别名，故不影响 design 判权。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        # design 云端不持有可按 project_id 查的项目行（见模块 docstring）。取该项目任一 active 分享行的
        # owner_hasn_id 作资源 owner；无任何 active 行（从未进入云端分享登记）→ None（门 404，存在性隐藏）。
        owner_hasn_id = (
            await db.execute(
                sa
                .select(HasnResourceShare.owner_hasn_id)
                .where(
                    HasnResourceShare.resource_type == _RESOURCE_TYPE_DESIGN,
                    HasnResourceShare.resource_id == resource_id,
                    HasnResourceShare.status == 'active',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not owner_hasn_id:
            return None
        # 可见性映射（不动语义）：design 分享为显式 grant 制 → personal/private，仅 owner_grant + 显式 share
        # 生效（与 design_share.py 泛型 ACL 一致）。云端无项目行，row 置 None（本地工具 handler 不消费云端行）。
        return ResourceMeta(
            resource_id=resource_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=None,
        )


def register() -> None:
    """把 design 项目资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    adapter = DesignProjectResourceAdapter()
    if adapter.resource_type not in resource_kind_registry.registered_types():
        resource_kind_registry.register(adapter)


# 模块导入即注册（Python 模块缓存保证进程内只跑一次）。design 工具是本地工具、不进云端 tools[]，
# 故 validate_manifest 不校验其 capabilities 的 resource_access（只查 tools[]），亦不要求本 adapter
# 在平台启动期注册；per-app 门测试 import 本模块即触发注册，保证门用到 adapter 时它已在注册表。
register()
