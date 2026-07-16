"""平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） 应用的 G6 统一资源权限门·资源适配器骨架（doc33 S3-5 codegen 生成）。

⚠️ 这是 codegen 生成的**骨架**——若本应用的资源**可被分享 / 需按资源实例判权**，请：
1. 填好 `load_meta`：据 `resource_id` 查本应用资源行，返回 `ResourceMeta`（owner/scope/visibility/enterprise），
   畸形 / 不存在返回 `None`（门据此 404 隐藏存在性，绝不冒 500）；
2. 在 `id_param_aliases` 列出工具入参里指代本资源 id 的参数名（供 S3-2 声明完整度守卫机械核对；
   跨应用重名的通用参名如 `project_id` 建议留空，避免误伤别的应用——见 studio/design adapter 先例）；
3. 取消文件末尾 `register()` 的注释，令模块导入即自注册；
4. 确保本模块在应用启动时被 import（挂进 `ai_native_app_registry` 的应用注册链）；
5. manifest（`hasn_project/manifest.py` 或平台工具声明）里，收本资源 id 的工具条目补 `resource_access`：
   `resource_access = [{'param': '<id入参名>', 'type': 'hasn_project', 'need': 'viewer|editor|manager'}]`。

不涉及分享 / 无资源实例 ACL 的应用（如公开只读语义）可整份删除本文件。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class HasnProjectResourceAdapter:
    """平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） 资源适配器：resource_type='hasn_project'。"""

    resource_type = 'hasn_project'
    # TODO(G6)：列出工具入参里指代本资源 id 的参数名（如 ('hasn_project_id',)）；留空 = 不进 S3-2 别名集。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        # TODO(G6)：据 resource_id 查本应用资源行 → 返回 ResourceMeta；畸形 / 不存在返回 None（门据此 404 不冒 500）。
        raise NotImplementedError('填好 load_meta：据 resource_id 查资源行并返回 ResourceMeta')


def register() -> None:
    """把本应用资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    adapter = HasnProjectResourceAdapter()
    if adapter.resource_type not in resource_kind_registry.registered_types():
        resource_kind_registry.register(adapter)


# TODO(G6)：填好上面的 load_meta 后取消下一行注释，令模块导入即自注册（在此之前保持不注册，
# S2-5 建 share 行 fail-closed 会挡住本类型的分享——「能分享、必能判」，这是**期望**的安全默认）。
# register()
