"""knowledge 应用的 G6 资源类型适配器（doc32 §4·doc33 S2-6）。

平台层 G6 门（`app/hasn/service/authz/resource_gate.py`）只知道「按权威 id 取元信息、有没有父链、
有没有维度②域限制」这几件事，判权内核/继承/审计全在平台层。本模块把知识库三类资源
（库 / 文档 / 目录）注册进平台注册表，让门代替 `tool_handlers` 里的手写 `authorize_*` 判权。

三类资源：
- `knowledge`（库）：自身即 share 主体；维度②（分身 kb 白名单）经 `agent_domain_grant` 钩子接入。
- `knowledge_doc`（文档）：`has_own_shares=True`（单个文档可独立分享，KBSHARE Slice 2），门取
  `max(文档级 share, 库级 share)`（复刻 `_effective_doc_permission`）；父链 → 所属库。
- `knowledge_folder`（目录）：无独立 share（`authorize_folder` 即委托 `authorize_kb`），纯继承库档位；父链 → 所属库。

维度②（doc32 §7.4 一期钩子承载）：门的 `restricted` 判定按 `meta.resource_id ∈ 白名单` 比对。库的
白名单本就是 kb_id，直接用；文档/目录的 leaf id 是 doc_id/folder_id，与白名单（kb_id）不同空间，
故子类型的钩子把「白名单 kb」翻译成「白名单 kb 下的全部 doc_id / folder_id」，令门的比对成立
（只对**分身自有资源**触发——门在 owner==subject.owner 时才调钩子，共享来的库/文档不受维度②裁剪）。

层次纪律（doc32 §3）：适配器属**应用层**，可自由 import 本应用的 `knowledge_service` / 模型；
依赖方向是「应用 → 平台」（把自己注册进平台注册表），平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model import HasnAssetBindings
from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_knowledge.model import Document, Folder, Kb
from backend.app.hasn_knowledge.model.document_version import DocumentVersion
from backend.app.hasn_knowledge.service.inline_assets import asset_ids_from_content
from backend.app.hasn_knowledge.service.knowledge_service import _resource_uri, knowledge_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _asset_ids_from_content(content: str | None) -> set[str]:
    """兼容既有测试/调用点的应用内别名。"""
    return asset_ids_from_content(content)


def _to_int(resource_id: str) -> int | None:
    """把权威 id 串转 int；畸形（非数字）返回 None（门据此 404，绝不冒 500——入参伪造是 G6 本职）。"""
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


class KbResourceAdapter:
    """知识库（库级）资源适配器：resource_type='knowledge'，leaf id = kb_id。"""

    resource_type: str = 'knowledge'
    id_param_aliases: tuple[str, ...] = ('kb_id',)

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        kb_id = _to_int(resource_id)
        if kb_id is None:
            return None
        kb = (await db.execute(sa.select(Kb).where(Kb.id == kb_id, Kb.deleted_time.is_(None)))).scalar_one_or_none()
        if kb is None:
            return None
        return ResourceMeta(
            resource_id=str(kb.id),
            owner_hasn_id=kb.owner_id,
            owner_scope=kb.scope or 'personal',
            enterprise_id=kb.enterprise_id,
            visibility=kb.visibility or 'private',
            row=kb,
        )

    async def agent_domain_grant(self, db: AsyncSession, owner_id: str, agent_hasn_id: str) -> tuple[str, list[Any]]:
        """维度②钩子：直接返回 (mode, kb_ids)——库的 leaf id 即 kb_id，门比对成立。"""
        grant = await knowledge_service.get_agent_grant(db, owner_id, agent_hasn_id)
        return grant['mode'], list(grant['kb_ids'] or [])


async def _restricted_child_ids(
    db: AsyncSession, owner_id: str, agent_hasn_id: str, child_model: Any
) -> tuple[str, list[Any]]:
    """子类型（文档/目录）维度②钩子的公共实现：把白名单 kb 翻译成白名单 kb 下的全部子资源 id。

    仅 `restricted` 需要枚举（把 kb 白名单落到子 id 空间，令门的 `meta.resource_id ∈ 白名单` 比对成立）；
    `inherit`/`denied` 无需子 id（inherit 不裁剪、denied 门直接判无权）。只在**分身自有资源**上触发
    （门在 owner==subject.owner 才调），枚举面即分身主人自有库，规模可控（S4 换通用表后此翻译退役）。
    """
    grant = await knowledge_service.get_agent_grant(db, owner_id, agent_hasn_id)
    mode = grant['mode']
    if mode != 'restricted':
        return mode, []
    kb_ids = [int(i) for i in (grant['kb_ids'] or [])]
    if not kb_ids:
        return 'restricted', []
    rows = (
        (
            await db.execute(
                sa.select(child_model.id).where(
                    child_model.owner_id == owner_id,
                    child_model.kb_id.in_(kb_ids),
                    child_model.deleted_time.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return 'restricted', [int(i) for i in rows]


class KbDocResourceAdapter:
    """知识库文档资源适配器：resource_type='knowledge_doc'，leaf id = doc_id，父链 → 所属库。

    文档可独立分享（`has_own_shares=True`），门取 `max(文档级 share, 库级 share)`。
    """

    resource_type: str = 'knowledge_doc'
    id_param_aliases: tuple[str, ...] = ('doc_id',)
    has_own_shares = True

    async def collect_asset_ids(self, db: AsyncSession, resource_id: str) -> set[str]:
        """收集当前及历史版本中真实渲染、且合法归属/绑定到本文档的资产。"""
        doc_id = _to_int(resource_id)
        if doc_id is None:
            return set()
        doc = (
            await db.execute(
                sa.select(Document.id, Document.owner_id, Document.content).where(
                    Document.id == doc_id,
                    Document.kind == 'native',
                    Document.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            return set()
        version_contents = (
            await db.execute(
                sa.select(DocumentVersion.content).where(DocumentVersion.document_id == doc_id)
            )
        ).scalars().all()
        referenced: set[str] = set()
        for content in [doc.content, *version_contents]:
            referenced.update(asset_ids_from_content(content))
        if not referenced:
            return set()

        resource_uri = _resource_uri('knowledge.document', doc_id)
        bound_assets: set[str] = set()
        if resource_uri:
            bound_assets = set(
                (
                    await db.execute(
                        sa.select(HasnAssetBindings.asset_id).where(
                            HasnAssetBindings.asset_id.in_(referenced),
                            HasnAssetBindings.resource_uri == resource_uri,
                            HasnAssetBindings.role == 'inline_image',
                            HasnAssetBindings.status == 'active',
                        )
                    )
                )
                .scalars()
                .all()
            )
        return referenced & bound_assets

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        doc_id = _to_int(resource_id)
        if doc_id is None:
            return None
        doc = (
            await db.execute(sa.select(Document).where(Document.id == doc_id, Document.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if doc is None:
            return None
        # 文档自身无 visibility/企业归属（只认显式 grant），故 owner_scope/visibility 取 personal/private
        # （复刻 `_effective_doc_permission`）；真实可见面由父链的库承载。owner 用冗余的 doc.owner_id（=库 owner）。
        return ResourceMeta(
            resource_id=str(doc.id),
            owner_hasn_id=doc.owner_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=doc,
        )

    async def resolve_parent(self, db: AsyncSession, resource_id: str) -> tuple[str, str] | None:
        doc_id = _to_int(resource_id)
        if doc_id is None:
            return None
        kb_id = (
            await db.execute(sa.select(Document.kb_id).where(Document.id == doc_id, Document.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if kb_id is None:
            return None
        return 'knowledge', str(kb_id)

    async def agent_domain_grant(self, db: AsyncSession, owner_id: str, agent_hasn_id: str) -> tuple[str, list[Any]]:
        return await _restricted_child_ids(db, owner_id, agent_hasn_id, Document)


class KbFolderResourceAdapter:
    """知识库目录资源适配器：resource_type='knowledge_folder'，leaf id = folder_id，父链 → 所属库。

    目录无独立 share（`authorize_folder` 即委托 `authorize_kb`），纯继承库档位（无 `has_own_shares`）。
    """

    resource_type: str = 'knowledge_folder'
    id_param_aliases: tuple[str, ...] = ('folder_id',)

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        folder_id = _to_int(resource_id)
        if folder_id is None:
            return None
        folder = (
            await db.execute(sa.select(Folder).where(Folder.id == folder_id, Folder.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if folder is None:
            return None
        # 目录无自身 share/可见面，档位纯继承库（has_own_shares 缺省 False → 门跳过自身查询、只判父链）。
        return ResourceMeta(
            resource_id=str(folder.id),
            owner_hasn_id=folder.owner_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=folder,
        )

    async def resolve_parent(self, db: AsyncSession, resource_id: str) -> tuple[str, str] | None:
        folder_id = _to_int(resource_id)
        if folder_id is None:
            return None
        kb_id = (
            await db.execute(sa.select(Folder.kb_id).where(Folder.id == folder_id, Folder.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if kb_id is None:
            return None
        return 'knowledge', str(kb_id)

    async def agent_domain_grant(self, db: AsyncSession, owner_id: str, agent_hasn_id: str) -> tuple[str, list[Any]]:
        return await _restricted_child_ids(db, owner_id, agent_hasn_id, Folder)


def register() -> None:
    """把知识库三类资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    for adapter in (KbResourceAdapter(), KbDocResourceAdapter(), KbFolderResourceAdapter()):
        if adapter.resource_type not in resource_kind_registry.registered_types():
            resource_kind_registry.register(adapter)


# 模块导入即注册（Python 模块缓存保证进程内只跑一次）。平台启动时经 ai_native_app_registry 触发导入；
# 知识库 handler / 测试用例 import 本模块同样触发，保证门用到 adapter 时它已在注册表里。
register()
