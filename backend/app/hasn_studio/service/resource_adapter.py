"""studio 应用的 G6 资源类型适配器（doc32 §4·doc33 S3-1）。

studio（统一视频引擎）有两类可分享产物：**项目**（studio_project：管线/素材/分镜的容器，editor 可改可派
渲染）与**成品**（studio_artifact：最终视频）。二者在 service 层（`studio_service.authorize_project` /
`authorize_artifact`）本就用同一判权内核 `resolve_effective_permission` 判权（人面与分身面共用）；G6 在统一
派发管线里多判一次是**防御纵深**（代价一次 SELECT）：分身经 manifest 工具面（gateway_internal handler）声明
`resource_access` 后，门在 ask 审批前先按同一内核判权，确定性无权先拒、不打扰主人审批。

**resource_type 逐字对齐 service（关键·门查 share 表靠它）**：studio 分享落 `hasn_resource_share` 时
resource_type 存的是**命名空间化**的 `'studio_project'` / `'studio_artifact'`（见 studio_service
`_RESOURCE_TYPE_PROJECT` / `_RESOURCE_TYPE_ARTIFACT`），故本 adapter 的 resource_type 必须与之逐字一致，
否则门经 `resolve_effective_permission` 查显式 share 行时对不上、判不出被分享者的档位。

**可见性映射（关键·不动语义）**：studio_project / studio_artifact 表**无 owner_scope/visibility/enterprise 列**
（doc22 §3.1 数据模型；不同于知识库 Kb）——分享是「纯显式 ACL」（owner ∪ 经 resource_share 显式共享）。故
`load_meta` 恒取保守默认 `owner_scope='personal'` / `visibility='private'` / `enterprise_id=None`：内核的
visibility_grant 永不命中，唯 owner_grant（分身代主人恒 manager）+ explicit_grant（显式 share 档位）生效，
与 service `_effective_project_permission` / `_effective_artifact_permission` 传的三参默认逐字一致（语义零漂移）。
studio 无软删（service `_load_*_unconstrained` 亦不过滤 deleted_time），故按 id 直取、不加删除过滤。

**`id_param_aliases` 为何留空（`()`）**：守卫 1（`test_resource_access_declaration_contract`）据别名集
按参数名**机械匹配**「工具入参命中别名却漏声明」。而 studio 的 `project_id` / `artifact_id` 是**跨应用
重名参数**（creator / film / imagelab 等也用同名参却指向各自不走 resource_share 的资源），把它们做别名会
令守卫误伤那些天然豁免的应用工具（判成漏声明）。运行时门**不用别名**——只据声明里的 `param` + `type`
定位入参与 adapter（见 `enforce_declaration`），故留空别名**不影响** studio 工具的运行时判权（仍按
manifest `resource_access` 声明逐个判）。studio 工具面的声明完整度改由 manifest 注册期校验 + 本应用
per-app 门测试锁死（与 plan 的留空别名同源，doc33 S3-1 记）。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_studio.model import StudioArtifact, StudioProject

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# studio 项目/成品无 visibility/scope/enterprise 列 → 纯显式 ACL，元信息恒取保守默认
# （与 studio_service `_DEFAULT_OWNER_SCOPE` / `_DEFAULT_VISIBILITY` 逐字一致，语义不动）。
_DEFAULT_OWNER_SCOPE = 'personal'
_DEFAULT_VISIBILITY = 'private'


def _to_int(resource_id: str) -> int | None:
    """把权威 id 串转 int；畸形（非数字）返回 None（门据此 404，绝不冒 500——入参伪造是 G6 本职）。"""
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


class StudioProjectResourceAdapter:
    """视频项目资源适配器：resource_type='studio_project'，leaf id = project_id（自身即 share 主体，无父链）。"""

    resource_type = 'studio_project'
    # 别名留空：`project_id` 是跨应用重名参（见模块 docstring「为何留空」），做别名会误伤天然豁免的
    # creator/film/imagelab 等同名参工具。运行时门据声明 param+type 判权，不用别名，故不影响 studio 判权。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        pid = _to_int(resource_id)
        if pid is None:
            return None
        project = (await db.execute(sa.select(StudioProject).where(StudioProject.id == pid))).scalar_one_or_none()
        if project is None:
            return None
        # 无可见性/企业列 → 恒保守默认（纯显式 ACL）：owner_grant + explicit_grant 生效，visibility_grant 不命中。
        return ResourceMeta(
            resource_id=str(project.id),
            owner_hasn_id=project.owner_hasn_id,
            owner_scope=_DEFAULT_OWNER_SCOPE,
            enterprise_id=None,
            visibility=_DEFAULT_VISIBILITY,
            row=project,
        )


class StudioArtifactResourceAdapter:
    """视频成品资源适配器：resource_type='studio_artifact'，leaf id = artifact_id（自身即 share 主体，无父链）。"""

    resource_type = 'studio_artifact'
    # 别名留空：`artifact_id` 亦为跨应用重名参（reel/film 等也用）。同上，不影响运行时判权。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        aid = _to_int(resource_id)
        if aid is None:
            return None
        artifact = (await db.execute(sa.select(StudioArtifact).where(StudioArtifact.id == aid))).scalar_one_or_none()
        if artifact is None:
            return None
        # 同项目：无可见性/企业列 → 恒保守默认（纯显式 ACL）。成品被独立分享（studio_artifact share 行）。
        return ResourceMeta(
            resource_id=str(artifact.id),
            owner_hasn_id=artifact.owner_hasn_id,
            owner_scope=_DEFAULT_OWNER_SCOPE,
            enterprise_id=None,
            visibility=_DEFAULT_VISIBILITY,
            row=artifact,
        )


def register() -> None:
    """把 studio 两类资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    for adapter in (StudioProjectResourceAdapter(), StudioArtifactResourceAdapter()):
        if adapter.resource_type not in resource_kind_registry.registered_types():
            resource_kind_registry.register(adapter)


# 模块导入即注册（Python 模块缓存保证进程内只跑一次）。平台启动经 ai_native_app_registry 触发导入；
# studio handler / 测试用例 import 本模块同样触发，保证门用到 adapter 时它已在注册表里。
register()
