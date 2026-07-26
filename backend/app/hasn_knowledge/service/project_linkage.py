"""知识库容器（kb）的平台项目挂靠 adapter 注册（doc38 层2 / 实施/97 A-C2）。

import 即把知识库容器级 LinkageAdapter 注册进 `project_linkage_registry`：
`knowledge/kbs` → `Kb.platform_project_id`（长生命周期容器：库建一次、用很久，可整体挂进项目）。

`domain` 必须与 manifest 里 `knowledge.base` 的 `ResourceDescriptor.uri_domain` 完全一致；
`kb.id` 是 bigserial 整型主键（`id_is_uuid=False`），`is_container=True` 参与项目总览并集读反查。

**`related_resource_uris` 是必做项**：只注册 adapter 不给钩子，并集读里就只有 `hasn://knowledge/kbs/{id}`
这一条 URI，库内文档产物（`hasn://knowledge/documents/{doc_id}`）一篇都进不来——主人挂靠一个既有库后
打开项目总览近似空白，正是 doc38 §5.1-4 要防的「挂了个寂寞」。

`revision_column` / `sync_kind` 留空：kb 表没有 revision 列，知识库也没有 owner 定向失效 kind
（镜像靠 daemon `commit_knowledge_mutation` + list read-through 刷新，webui 挂靠后主动 invalidate 查询）。
项目=视角，非权限边界/挂载点/容器接管（doc38 三铁律）。由 ai_native_app_registry 在 import 链上加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_knowledge.model.document import Document
from backend.app.hasn_knowledge.model.kb import Kb
from backend.app.hasn_project.service.project_linkage_registry import LinkageAdapter, project_linkage_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _build_uri(resource_kind: str, server_id: object) -> str:
    """经 manifest descriptor 构造云端权威资源 URI，禁止在 adapter 手拼域。"""
    # 延迟导入，避免 ai_native_app_registry 加载本注册模块时形成循环导入。
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    descriptor = ai_native_app_registry.resource_descriptor('knowledge', resource_kind)
    if descriptor is None:
        raise RuntimeError(f'knowledge descriptor 缺失：{resource_kind}')
    return descriptor.build_uri(str(server_id))


async def _kb_related_uris(db: AsyncSession, owner: str, rows: tuple[Any, ...]) -> list[str]:
    """取已挂靠知识库名下的文档 URI（库挂进项目 → 库里既有文档的产物也进项目产物流）。"""
    kb_ids = [row.id for row in rows]
    if not kb_ids:
        return []
    doc_ids = (
        (
            await db.execute(
                sa.select(Document.id).where(
                    Document.owner_id == owner,
                    Document.kb_id.in_(kb_ids),
                    Document.deleted_time.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return [_build_uri('knowledge.document', server_id) for server_id in doc_ids]


# 知识库容器：主人的长期知识资产，可整体挂进项目（doc38 §3.2 三类挂靠点·容器级）
project_linkage_registry.register(
    LinkageAdapter(
        domain='knowledge/kbs',
        model=Kb,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='knowledge',
        kind='knowledge_base',
        title_column='name',
        related_resource_uris=_kb_related_uris,
    )
)
