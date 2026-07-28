"""知识库云端业务服务（knowledge 应用唯一数据面执行点）。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2/§4/§5。

要点：
- 云端 PG 权威（元数据 + 原生正文 + 权限）+ 平台私有桶（file 原件，D10）；
  RAGFlow 只持可重建派生物（解析副本/分块/向量），本 service 是其唯一调用方。
- app scope（Owner JWT）与 agent scope（Agent JWT）共用同一组方法，身份解析在 api 层完成，
  service 只认 owner_id（HASN ID）隔离键；跨 owner 访问按「不存在」处理。
- 零 fake：引擎失败如实落 parse_status=failed + parse_error；「确无命中」与「检索故障」严格区分。
"""

from __future__ import annotations

import mimetypes
import re
import uuid

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy import func, select

from backend.app.hasn.model.hasn_ai_native_app_audit import HasnAiNativeAppAudit
from backend.app.hasn.service.authz import Subject  # G6：收编来源，模块级再导出（既有调用点不变）
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.app.hasn.service.resource_share_service import rank, resource_share_service
from backend.app.hasn_knowledge.model import AgentKbGrant, Document, DocumentVersion, Folder, Kb
from backend.app.hasn_knowledge.service.instance import resolve_knowledge_instance
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import storage_service
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# 文件上传上限（对齐 hasn_assets file 限额）
MAX_FILE_SIZE = 50 * 1024 * 1024
# 原生文档正文上限（5000 字符，按 Unicode 码点计——中文 1 字 = 1）。
# 知识库铁律「原生优先，能不落 file 就不落」：原生文档可编辑、有版本、Markdown 渲染最好，
# 定位为「小而互连的 wiki 式笔记」。内容超限即应拆成多篇更聚焦的原生文档，
# 用深链 hasn://knowledge/documents/{doc_id} 互相关联，而非堆成一篇长文，
# 也**不**自动回落 file（file 编辑成本高）——create/update/upload 三条写入路径超限均如实拒绝、引导拆分。
# 只有真实二进制文件（PDF/docx/图片，经 asset_uri 上传）才落 file 文档，由引擎切块承载。
MAX_NATIVE_CONTENT_CHARS = 5000
# 文档深链 URI：hasn://knowledge/documents/{doc_id}，{doc_id} 为云端权威文档 id（纯数字）。
# 正文里无论裸写还是包在 Markdown 链接 [标题](hasn://knowledge/documents/123) 中都能被捕获。
# 客户端无关 + 云端权威 id（见父仓 CLAUDE.md「hasn:// 资源地址客户端无关」「本地 ID 永不上 URI」两铁律）。
_DOC_LINK_RE = re.compile(r'hasn://knowledge/documents/(\d+)')
# folder_id 查询参数的「库根」哨兵（真实 id 从 1 起）
ROOT_FOLDER_SENTINEL = 0
_owner_storage = OwnerStorageService(async_db_session)

_GRANT_MODES = ('inherit', 'restricted', 'denied')
_CLIENT_REQUEST_ID_MAX_LENGTH = 200

# 知识库接入平台产物级协作（应用平台 v3 §6）：resource_share 的 resource_type。
_RESOURCE_TYPE = 'knowledge'
# 单个文档级协作：与库级（knowledge）平行的 resource_type，resource_id = doc_id。
# 文档级共享叠加在库级之上（取高者）；文档自身无 visibility/enterprise，故只认显式 grant。
_RESOURCE_TYPE_DOC = 'knowledge_doc'


def _resource_uri(resource_kind: str, server_id: int) -> str | None:
    """算这条知识库资源的 `hasn://` 地址——经 manifest descriptor 的统一 builder（doc36 §3.1/§3.3）。

    **不在这里手拼字符串**：URI 的唯一拼接点是 `ResourceDescriptor.build_uri`，manifest 的 `uri_domain`
    是唯一事实源。各处各拼一份 `f'hasn://knowledge/...'`，就是 doc36 §1.3 盘出的「N 处字面量 +
    与 manifest 声明对不上」的来路。

    `_kb_dict`/`_document_dict` 是**读路径**（list/get 也走）——正是「单点必须是拼接函数、而非把写路径
    算好的值透传下去」的实证：读路径没有登记回执可透传，只能自己调 builder。

    descriptor 解析不出（manifest 没声明）→ 返 `None`，投影省略 `uri` 字段，绝不返空串或假 URI。
    """
    from backend.app.hasn_core.app_platform import ai_native_app_registry

    descriptor = ai_native_app_registry.resource_descriptor('knowledge', resource_kind)
    if descriptor is None or descriptor.resource_kind != resource_kind:
        return None
    return descriptor.build_uri(server_id)


def _as_project_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """把入参项目 id 归一成 UUID；非法格式如实报 400（不静默忽略，避免「过滤了个寂寞」）。"""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (AttributeError, TypeError, ValueError) as exc:
        raise errors.RequestError(msg=f'项目 id 不是合法 UUID：{value!r}') from exc


def _normalize_client_request_id(value: str | None) -> str | None:
    """归一建库业务幂等键；空值保持旧版非幂等创建语义。"""
    if value is None:
        return None
    request_id = str(value).strip()
    if not request_id:
        raise errors.RequestError(
            msg='client_request_id 不能为空',
            data={'error_code': 'KNOWLEDGE_CLIENT_REQUEST_ID_INVALID'},
        )
    if len(request_id) > _CLIENT_REQUEST_ID_MAX_LENGTH:
        raise errors.RequestError(
            msg=f'client_request_id 最长 {_CLIENT_REQUEST_ID_MAX_LENGTH} 个字符',
            data={'error_code': 'KNOWLEDGE_CLIENT_REQUEST_ID_INVALID'},
        )
    return request_id


def _same_kb_create_payload(
    kb: Kb,
    *,
    name: str,
    description: str | None,
    cover_asset_uri: str | None,
    platform_project_id: uuid.UUID | None,
) -> bool:
    """判断幂等重放是否与首次建库参数完全一致。"""
    return (
        kb.name == name
        and kb.description == description
        and kb.cover_asset_uri == cover_asset_uri
        and kb.platform_project_id == platform_project_id
        and kb.deleted_time is None
    )


def _idempotent_kb_result(kb: Kb, *, replay: bool) -> dict[str, Any]:
    """序列化建库结果，并显式告诉调用方是否为幂等重放。"""
    result = _kb_dict(kb)
    result['idempotent_replay'] = replay
    return result


def _kb_dict(kb: Kb, *, my_permission: str | None = None, relation: str | None = None) -> dict[str, Any]:
    out = {
        'id': kb.id,
        # 库深链（doc36 §3.3 补齐）：此前**只有文档有 uri、库没有**——分身建完知识库只拿到一个裸
        # `kb_id`，不知道怎么打开它；往里写一篇文档反而拿得到地址。同一应用内两类资源的不对称到此为止。
        'uri': _resource_uri('knowledge.base', kb.id),
        'owner_id': kb.owner_id,
        'name': kb.name,
        'description': kb.description,
        # 封面存 hasn://asset/{id}（不存 CDN 直链）；webui 渲染边界经 /api/v1/assets/signed-url 换签名 URL。
        'cover_asset_uri': kb.cover_asset_uri,
        'scope': kb.scope,
        'enterprise_id': kb.enterprise_id,
        'visibility': kb.visibility,
        'embedding_model': kb.embedding_model,
        'document_count': kb.document_count,
        'chunk_count': kb.chunk_count,
        'status': kb.status,
        # 平台项目挂靠（doc38 层2 / doc §3.4）：读侧**不按项目默认收窄**，改为每行回带归属，
        # 分身与 webui 据此自证「这个库属于哪个项目」，需要收窄时再显式传过滤参。
        'platform_project_id': str(kb.platform_project_id) if kb.platform_project_id else None,
        'created_time': kb.created_time,
        'updated_time': kb.updated_time,
    }
    if my_permission is not None:
        out['my_permission'] = my_permission
    if relation is not None:
        out['relation'] = relation
    return out


def _folder_dict(f: Folder) -> dict[str, Any]:
    return {
        'id': f.id,
        'kb_id': f.kb_id,
        'parent_id': f.parent_id,
        'name': f.name,
        'sort_order': f.sort_order,
        'created_time': f.created_time,
        'updated_time': f.updated_time,
    }


def _document_dict(d: Document, *, with_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        'id': d.id,
        # 文档深链（客户端无关 + 云端权威 id）：卡片/正文互连、webui 点击跳转都用它，
        # 分身撰写深链时也照这个格式引用同库其它文档。
        # doc36 §3.1：改经统一 builder，不再手拼字面量（原 `f'hasn://knowledge/documents/{id}'`）——
        # 手拼的那一刻就和 manifest 的 uri_domain 脱钩了，改声明时这里不会跟着变、也不会报错。
        'uri': _resource_uri('knowledge.document', d.id),
        'kb_id': d.kb_id,
        'folder_id': d.folder_id,
        'kind': d.kind,
        'name': d.name,
        'size_bytes': d.size_bytes,
        'mime_type': d.mime_type,
        'current_version': d.current_version,
        'parse_status': d.parse_status,
        'parse_error': d.parse_error,
        'chunk_count': d.chunk_count,
        'source': d.source,
        'agent_hasn_id': d.agent_hasn_id,
        'created_time': d.created_time,
        'updated_time': d.updated_time,
    }
    if with_content:
        data['content'] = d.content
    return data


def _version_dict(v: DocumentVersion, *, with_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        'id': v.id,
        'document_id': v.document_id,
        'version_no': v.version_no,
        'title': v.title,
        'source': v.source,
        'agent_hasn_id': v.agent_hasn_id,
        'created_time': v.created_time,
    }
    if with_content:
        data['content'] = v.content
    return data


def _native_filename(title: str) -> str:
    """原生文档推 RAGFlow 的副本文件名（.md；引擎内仅为派生物标识）。"""
    safe = ''.join(c for c in title if c not in '/\\:*?"<>|').strip() or 'untitled'
    return f'{safe[:80]}.md'


def _extract_unowned_dataset_id(message: str, candidate_ids: list[str]) -> str | None:
    """从 RagFlow「you don't own the dataset {id}」类错误信息里识别出具体的孤儿 dataset id。

    仅在错误信息里确实包含某个候选 id 时才返回（避免把无关业务错误误判成孤儿 dataset）。
    """
    for candidate in candidate_ids:
        if candidate in message:
            return candidate
    return None


class KnowledgeService:
    # ---------- kb ----------

    async def _get_kb(self, db: AsyncSession, resource_owner_id: str, kb_id: int) -> Kb:
        kb = (
            await db.execute(
                select(Kb).where(Kb.id == kb_id, Kb.owner_id == resource_owner_id, Kb.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if kb is None:
            raise errors.NotFoundError(msg='知识库不存在')
        return kb

    # ---------- 产物级协作：权限闸（app/human 路；agent 路仍走 owner_id+维度② grant）----------
    #
    # 复用纪律：现有 owner_id keyed 方法（文档/目录/正文/检索）一字不动——文档/目录行的
    # owner_id 恒等于其所属库的 owner_id，所以「先 authorize 闸，再用 kb.owner_id 委托旧方法」
    # 对库主人与被分享者都返回正确结果，零重写、零回归。被分享编辑者新建的行也随 kb.owner_id
    # 归属库（对齐 deck：page.owner_id = deck.owner_id）。

    async def _load_kb(self, db: AsyncSession, kb_id: int) -> Kb:
        """按 id 取未删 kb（不做 owner 过滤；access 交给权限闸）。"""
        kb = (
            await db.execute(select(Kb).where(Kb.id == kb_id, Kb.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if kb is None:
            raise errors.NotFoundError(msg='知识库不存在')
        return kb

    async def _effective_permission(self, db: AsyncSession, *, kb: Kb, subject: Subject) -> str:
        return await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=subject.hasn_id,
            subject_kind=subject.kind,
            subject_owner_hasn_id=subject.owner_hasn_id,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(kb.id),
            resource_owner_hasn_id=kb.owner_id,
            resource_owner_scope=kb.scope or 'personal',
            resource_enterprise_id=kb.enterprise_id,
            resource_visibility=kb.visibility or 'private',
        )

    async def authorize_kb(self, db: AsyncSession, *, subject: Subject, kb_id: int, need: str) -> Kb:
        """校验 subject 对 kb 至少有 need 权限；不足则报错（none→不存在不泄露，其它→无权限）。返回 kb。"""
        kb = await self._load_kb(db, kb_id)
        eff = await self._effective_permission(db, kb=kb, subject=subject)
        if rank(eff) < rank(need):
            if rank(eff) == 0:
                raise errors.NotFoundError(msg='知识库不存在')
            raise errors.ForbiddenError(msg='没有该操作权限')
        return kb

    async def _effective_doc_permission(self, db: AsyncSession, *, doc: Document, kb: Kb, subject: Subject) -> str:
        """文档级显式协作授权（叠加在库级之上）。文档无可见性/企业归属，只认 hasn_resource_share 显式 grant。"""
        return await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=subject.hasn_id,
            subject_kind=subject.kind,
            subject_owner_hasn_id=subject.owner_hasn_id,
            resource_type=_RESOURCE_TYPE_DOC,
            resource_id=str(doc.id),
            resource_owner_hasn_id=kb.owner_id,
            resource_owner_scope='personal',
            resource_enterprise_id=None,
            resource_visibility='private',
        )

    async def authorize_doc(self, db: AsyncSession, *, subject: Subject, doc_id: int, need: str) -> Kb:
        """按 doc_id 反查所属 kb，校验「库级权限 ∪ 文档级共享」取高者；返回 kb（caller 用 kb.owner_id 委托旧方法）。

        被分享单个文档的协作者**没有**整库访问（不在 list_accessible_kbs 里），但凭文档级 grant 仍能据
        云端 doc_id read-through 打开该文档（HASN URI 铁律：据云端权威 id 从云端读，ACL 云端判权）。
        """
        doc = (
            await db.execute(select(Document).where(Document.id == doc_id, Document.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if doc is None:
            raise errors.NotFoundError(msg='文档不存在')
        kb = await self._load_kb(db, doc.kb_id)
        kb_eff = await self._effective_permission(db, kb=kb, subject=subject)
        doc_eff = await self._effective_doc_permission(db, doc=doc, kb=kb, subject=subject)
        eff = kb_eff if rank(kb_eff) >= rank(doc_eff) else doc_eff
        if rank(eff) < rank(need):
            if rank(eff) == 0:
                raise errors.NotFoundError(msg='文档不存在')
            raise errors.ForbiddenError(msg='没有该操作权限')
        return kb

    async def authorize_folder(self, db: AsyncSession, *, subject: Subject, folder_id: int, need: str) -> Kb:
        """按 folder_id 反查所属 kb 并校验权限；返回 kb。"""
        folder = (
            await db.execute(select(Folder).where(Folder.id == folder_id, Folder.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if folder is None:
            raise errors.NotFoundError(msg='目录不存在')
        return await self.authorize_kb(db, subject=subject, kb_id=folder.kb_id, need=need)

    async def _accessible_kb_rows(self, db: AsyncSession, *, subject: Subject) -> list[tuple[Kb, str, str]]:
        """可访问知识库的**单一事实源**：返回 (kb, my_permission, relation) 三元组。

        = 我拥有的（relation=owner，manager 权）∪ 共享给我的（relation=shared）∪ 我企业可见的
        （relation=enterprise）；非 owner 项经 `_effective_permission` 过滤掉 rank 0（无权）。

        `list_accessible_kbs`（浏览）与 `resolve_retrieval_visible_kbs`（检索）都建立在此之上，
        确保「浏览可见集」与「检索可见集」永不漂移——分享/隔离在此单点收口。
        """
        human = subject.owner_hasn_id
        memberships = await resource_share_service.acting_human_memberships(db, human)
        member_enterprise_ids = {eid for eid, _ in memberships}

        # 1. 我拥有的（保持原 owner 隔离语义，relation=owner / manager）
        owned = (
            (
                await db.execute(
                    select(Kb).where(Kb.owner_id == human, Kb.deleted_time.is_(None)).order_by(Kb.id.desc())
                )
            )
            .scalars()
            .all()
        )
        owned_ids = {kb.id for kb in owned}

        # 2. 共享给我的（直接给人 / 给我企业 / 给我这个分身）
        shared_ids: set[str] = set(
            await resource_share_service.shared_resource_ids_for_human(
                db, resource_type=_RESOURCE_TYPE, human_hasn_id=human
            )
        )
        if subject.kind == 'agent':
            shared_ids |= await self._shared_kb_ids_for_agent(db, agent_hasn_id=subject.hasn_id)

        # 3. 企业可见的库 id
        ent_ids: set[int] = set()
        if member_enterprise_ids:
            ent_ids = set(
                (
                    await db.execute(
                        select(Kb.id).where(
                            Kb.deleted_time.is_(None),
                            Kb.scope == 'enterprise',
                            Kb.visibility == 'enterprise',
                            Kb.enterprise_id.in_(member_enterprise_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )

        extra_ids = {int(i) for i in shared_ids if i.isdigit()} | ent_ids
        extra_ids -= owned_ids
        extra_kbs: list[Kb] = []
        if extra_ids:
            extra_kbs = list(
                (await db.execute(select(Kb).where(Kb.id.in_(extra_ids), Kb.deleted_time.is_(None)))).scalars().all()
            )

        rows: list[tuple[Kb, str, str]] = [(kb, 'manager', 'owner') for kb in owned]
        for kb in extra_kbs:
            eff = await self._effective_permission(db, kb=kb, subject=subject)
            if rank(eff) == 0:
                continue
            relation = 'enterprise' if (kb.id in ent_ids and str(kb.id) not in shared_ids) else 'shared'
            rows.append((kb, eff, relation))
        return rows

    async def list_accessible_kbs(
        self, db: AsyncSession, *, subject: Subject, platform_project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """可访问知识库 = 我拥有的 ∪ 共享给我的 ∪ 我企业可见的，每条带 relation + my_permission。

        `platform_project_id` 非空时按挂靠项目收窄（缺省不过滤，见 `list_kbs` 口径说明）。
        """
        pid = _as_project_uuid(platform_project_id) if platform_project_id else None
        return [
            _kb_dict(kb, my_permission=perm, relation=relation)
            for kb, perm, relation in await self._accessible_kb_rows(db, subject=subject)
            if pid is None or kb.platform_project_id == pid
        ]

    @staticmethod
    async def _shared_kb_ids_for_agent(db: AsyncSession, *, agent_hasn_id: str) -> set[str]:
        from backend.app.hasn.model import HasnResourceShare

        rows = (
            (
                await db.execute(
                    select(HasnResourceShare.resource_id).where(
                        HasnResourceShare.resource_type == _RESOURCE_TYPE,
                        HasnResourceShare.status == 'active',
                        HasnResourceShare.grantee_type == 'agent',
                        HasnResourceShare.grantee_id == agent_hasn_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    # ---------- 共享管理（manager 权）----------

    async def list_shares(self, db: AsyncSession, *, subject: Subject, kb_id: int) -> dict[str, Any]:
        kb = await self.authorize_kb(db, subject=subject, kb_id=kb_id, need='manager')
        shares = await resource_share_service.list_shares(db, resource_type=_RESOURCE_TYPE, resource_id=str(kb_id))
        return {
            'kb_id': kb_id,
            'scope': kb.scope,
            'enterprise_id': kb.enterprise_id,
            'visibility': kb.visibility,
            'shares': shares,
        }

    async def set_visibility(
        self, db: AsyncSession, *, subject: Subject, kb_id: int, visibility: str, enterprise_id: int | None = None
    ) -> dict[str, Any]:
        kb = await self.authorize_kb(db, subject=subject, kb_id=kb_id, need='manager')
        if visibility not in ('private', 'enterprise', 'link'):
            raise errors.RequestError(msg='非法可见性')
        if visibility == 'enterprise':
            target_ent = enterprise_id if enterprise_id is not None else kb.enterprise_id
            if target_ent is None:
                raise errors.ForbiddenError(msg='个人知识库需先归属企业才能设为企业可见')
            memberships = await resource_share_service.acting_human_memberships(db, subject.owner_hasn_id)
            if target_ent not in {eid for eid, _ in memberships}:
                raise errors.ForbiddenError(msg='你不是该企业成员')
            kb.scope = 'enterprise'
            kb.enterprise_id = target_ent
        kb.visibility = visibility
        kb.updated_time = timezone.now()
        await db.flush()
        return _kb_dict(kb)

    async def add_share(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        kb_id: int,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        if grantee_type not in ('human', 'agent', 'enterprise'):
            raise errors.RequestError(msg='仅支持 human/agent/enterprise 协作者')
        if permission not in ('viewer', 'editor', 'manager'):
            raise errors.RequestError(msg='非法权限档')
        kb = await self.authorize_kb(db, subject=subject, kb_id=kb_id, need='manager')
        return await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(kb_id),
            owner_hasn_id=kb.owner_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=subject.hasn_id,
        )

    async def revoke_share(
        self, db: AsyncSession, *, subject: Subject, kb_id: int, grantee_type: str, grantee_id: str
    ) -> bool:
        await self.authorize_kb(db, subject=subject, kb_id=kb_id, need='manager')
        return await resource_share_service.revoke_share(
            db, resource_type=_RESOURCE_TYPE, resource_id=str(kb_id), grantee_type=grantee_type, grantee_id=grantee_id
        )

    # ---------- 单个文档级共享（manager 权；文档协作者仅 human/agent，无可见性档）----------

    async def list_doc_shares(self, db: AsyncSession, *, subject: Subject, doc_id: int) -> dict[str, Any]:
        kb = await self.authorize_doc(db, subject=subject, doc_id=doc_id, need='manager')
        shares = await resource_share_service.list_shares(
            db, resource_type=_RESOURCE_TYPE_DOC, resource_id=str(doc_id)
        )
        return {'doc_id': doc_id, 'kb_id': kb.id, 'shares': shares}

    async def add_doc_share(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        doc_id: int,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        if grantee_type not in ('human', 'agent'):
            raise errors.RequestError(msg='文档分享仅支持 human/agent 协作者')
        if permission not in ('viewer', 'editor', 'manager'):
            raise errors.RequestError(msg='非法权限档')
        kb = await self.authorize_doc(db, subject=subject, doc_id=doc_id, need='manager')
        doc = (
            await db.execute(select(Document).where(Document.id == doc_id, Document.deleted_time.is_(None)))
        ).scalar_one_or_none()
        share = await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE_DOC,
            resource_id=str(doc_id),
            owner_hasn_id=kb.owner_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=subject.hasn_id,
        )
        # 回带文档标题/库 id，供 daemon 直接组卡（无需另查），对齐「daemon 据云端权威 id 组卡」纪律
        return {**share, 'doc_id': doc_id, 'kb_id': kb.id, 'doc_name': doc.name if doc else ''}

    async def revoke_doc_share(
        self, db: AsyncSession, *, subject: Subject, doc_id: int, grantee_type: str, grantee_id: str
    ) -> bool:
        await self.authorize_doc(db, subject=subject, doc_id=doc_id, need='manager')
        return await resource_share_service.revoke_share(
            db, resource_type=_RESOURCE_TYPE_DOC, resource_id=str(doc_id), grantee_type=grantee_type, grantee_id=grantee_id
        )

    async def get_kb_detail(self, db: AsyncSession, *, subject: Subject, kb_id: int) -> dict[str, Any]:
        """单库详情（viewer 权即可读）：含 `platform_project_id`，供 daemon 继承挂靠项目与 webui 详情页用。"""
        kb = await self.authorize_kb(db, subject=subject, kb_id=kb_id, need='viewer')
        eff = await self._effective_permission(db, kb=kb, subject=subject)
        relation = 'owner' if kb.owner_id == subject.hasn_id else 'shared'
        return _kb_dict(kb, my_permission=eff, relation=relation)

    async def list_kbs(
        self, db: AsyncSession, owner_id: str, *, platform_project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列主人自己的库；`platform_project_id` 非空时收窄到该项目（项目总览「挂靠资源区」用）。

        缺省**不过滤**——知识库是长期资产、跨项目复用是常态，默认按当前项目收窄会让分身/主人
        看不见绝大多数库（doc38 §5.6「写侧继承、读侧不收窄」）。
        """
        conditions = [Kb.owner_id == owner_id, Kb.deleted_time.is_(None)]
        if platform_project_id:
            conditions.append(Kb.platform_project_id == _as_project_uuid(platform_project_id))
        rows = (await db.execute(select(Kb).where(*conditions).order_by(Kb.id.desc()))).scalars()
        return [_kb_dict(kb) for kb in rows]

    @staticmethod
    async def _resolve_owned_project_id(
        db: AsyncSession, *, owner_id: str, platform_project_id: str | None
    ) -> uuid.UUID | None:
        """校验并归一「新库要挂进哪个平台项目」：空 → None（不挂）；非本主人项目 → 404。

        延迟导入 project 域，避免应用间 import 环（knowledge 只在此一处依赖 project service）。
        """
        if not platform_project_id or not str(platform_project_id).strip():
            return None
        from backend.app.hasn_project.service.project_app_service import project_service

        pid = _as_project_uuid(platform_project_id)
        await project_service.assert_owned(db, owner=owner_id, pk=pid)
        return pid

    async def create_kb(
        self,
        db: AsyncSession,
        owner_id: str,
        *,
        name: str,
        description: str | None,
        cover_asset_uri: str | None = None,
        platform_project_id: str | None = None,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """建库：先建 RAGFlow dataset（失败则不落 kb 行，如实报错），成功后落域行。

        cover_asset_uri：封面资产引用（hasn://asset/{id}），主人建库上传或分身建库配图得到；
        存原始引用、不存 CDN 直链，渲染边界再换签名 URL。

        platform_project_id：新库直接挂进的平台项目（doc38 §5.5 容器创建时的项目归属）。
        三条来源——① daemon 代主人建库时带上本次派发定稿的项目；② 分身在项目工作会话里调
        `create_kb` 时由 ContextVar 缺省；③ 分身显式指名项目（先经 `hasn.project.list` 换权威 UUID）。
        **写前必过归属校验**：非本主人的项目 → 404，绝不直写列（否则绕过挂靠点注册表的 owner 隔离）。
        """
        normalized_name = name.strip()
        normalized_description = description.strip() if description and description.strip() else None
        normalized_cover = cover_asset_uri.strip() if cover_asset_uri and cover_asset_uri.strip() else None
        request_id = _normalize_client_request_id(client_request_id)
        project_id = await self._resolve_owned_project_id(db, owner_id=owner_id, platform_project_id=platform_project_id)
        if request_id is not None:
            # 事务级 advisory lock 在调用真实 RAGFlow 前串行化同 Owner+幂等键并发，
            # 避免数据库唯一约束虽挡住重复行，却留下第二个外部 dataset。
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': f'knowledge:create:{owner_id}:{request_id}'},
            )
            existing = (
                await db.execute(
                    select(Kb).where(
                        Kb.owner_id == owner_id,
                        Kb.client_request_id == request_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if not _same_kb_create_payload(
                    existing,
                    name=normalized_name,
                    description=normalized_description,
                    cover_asset_uri=normalized_cover,
                    platform_project_id=project_id,
                ):
                    raise errors.ConflictError(
                        msg='client_request_id 已用于不同的知识库创建参数',
                        data={'error_code': 'KNOWLEDGE_IDEMPOTENCY_CONFLICT'},
                    )
                return _idempotent_kb_result(existing, replay=True)
        client, config = await resolve_knowledge_instance(db)
        # dataset 内部名取唯一随机串（单平台租户下避免撞名；展示名只活在域行）
        dataset = await client.create_dataset(name=f'hxkb_{uuid.uuid4().hex[:16]}', embedding_model=config.default_embd_id)
        kb = Kb(
            owner_id=owner_id,
            scope='personal',
            enterprise_id=None,
            name=normalized_name,
            description=normalized_description,
            cover_asset_uri=normalized_cover,
            ragflow_dataset_id=str(dataset['id']),
            embedding_model=config.default_embd_id,
            document_count=0,
            chunk_count=0,
            status='active',
            platform_project_id=project_id,
            client_request_id=request_id,
        )
        db.add(kb)
        await db.flush()
        return _idempotent_kb_result(kb, replay=False) if request_id else _kb_dict(kb)

    async def update_kb(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        kb_id: int,
        *,
        name: str | None,
        description: str | None,
        cover_asset_uri: str | None = None,
    ) -> dict[str, Any]:
        """改库名 / 描述 / 封面：只动域行的展示元数据，不碰 RAGFlow dataset（内部名与索引不变）。

        权限已在 caller（app 端点）用 authorize_kb(need='manager') 兜过；这里按 kb.owner_id
        取行后原地改字段。name 传空/仅空白则不动库名（避免误清空）；description 显式传入即覆盖；
        cover_asset_uri 显式传入即覆盖（空串=清空封面）。
        """
        kb = await self._get_kb(db, resource_owner_id, kb_id)
        if name is not None and name.strip():
            kb.name = name.strip()
        if description is not None:
            kb.description = description.strip() or None
        if cover_asset_uri is not None:
            kb.cover_asset_uri = cover_asset_uri.strip() or None
        await db.flush()
        return _kb_dict(kb)

    async def delete_kb(self, db: AsyncSession, resource_owner_id: str, kb_id: int) -> None:
        """删库：级联删 RAGFlow dataset + 文档/目录行。引擎不可达如实报错（避免残留孤儿向量可检索）。"""
        kb = await self._get_kb(db, resource_owner_id, kb_id)
        client, _ = await resolve_knowledge_instance(db)
        try:
            await client.delete_datasets(ids=[kb.ragflow_dataset_id])
        except KnowledgeProviderError as exc:
            if exc.code != 'knowledge_provider_error':
                raise
            # dataset 已不存在等业务错误 → 域行照删（派生物已不在）
        now = timezone.now()
        kb.deleted_time = now
        await db.execute(
            sa.update(Document)
            .where(Document.kb_id == kb_id, Document.deleted_time.is_(None))
            .values(deleted_time=now)
        )
        await db.execute(
            sa.update(Folder).where(Folder.kb_id == kb_id, Folder.deleted_time.is_(None)).values(deleted_time=now)
        )

    # ---------- folders（D9 目录树）----------

    async def _get_folder(self, db: AsyncSession, resource_owner_id: str, folder_id: int) -> Folder:
        folder = (
            await db.execute(
                select(Folder).where(
                    Folder.id == folder_id, Folder.owner_id == resource_owner_id, Folder.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if folder is None:
            raise errors.NotFoundError(msg='目录不存在')
        return folder

    async def get_folder(self, db: AsyncSession, resource_owner_id: str, folder_id: int) -> dict[str, Any]:
        """读单个目录（owner 隔离）；供 agent 面按 folder_id 反查所属 kb 做可达性闸门。"""
        return _folder_dict(await self._get_folder(db, resource_owner_id, folder_id))

    async def list_folders(self, db: AsyncSession, resource_owner_id: str, kb_id: int) -> list[dict[str, Any]]:
        await self._get_kb(db, resource_owner_id, kb_id)
        rows = (
            await db.execute(
                select(Folder)
                .where(Folder.kb_id == kb_id, Folder.deleted_time.is_(None))
                .order_by(Folder.sort_order, Folder.id)
            )
        ).scalars()
        return [_folder_dict(f) for f in rows]

    async def _assert_sibling_name_free(
        self, db: AsyncSession, kb_id: int, parent_id: int | None, name: str, *, exclude_id: int | None = None
    ) -> None:
        stmt = select(Folder.id).where(
            Folder.kb_id == kb_id,
            Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id,
            Folder.name == name,
            Folder.deleted_time.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(Folder.id != exclude_id)
        if (await db.execute(stmt.limit(1))).first() is not None:
            raise errors.ConflictError(msg='同层已存在同名目录')

    async def create_folder(
        self, db: AsyncSession, resource_owner_id: str, kb_id: int, *, name: str, parent_id: int | None = None
    ) -> dict[str, Any]:
        await self._get_kb(db, resource_owner_id, kb_id)
        if parent_id is not None:
            parent = await self._get_folder(db, resource_owner_id, parent_id)
            if parent.kb_id != kb_id:
                raise errors.RequestError(msg='父目录不属于该知识库')
        await self._assert_sibling_name_free(db, kb_id, parent_id, name)
        folder = Folder(kb_id=kb_id, owner_id=resource_owner_id, parent_id=parent_id, name=name, sort_order=0)
        db.add(folder)
        await db.flush()
        return _folder_dict(folder)

    async def _is_descendant(self, db: AsyncSession, kb_id: int, candidate_id: int, ancestor_id: int) -> bool:
        """candidate 是否 ancestor 的子孙（环检测：沿 parent 链上溯）。"""
        current: int | None = candidate_id
        for _ in range(100):
            if current is None:
                return False
            if current == ancestor_id:
                return True
            current = (
                await db.execute(
                    select(Folder.parent_id).where(Folder.id == current, Folder.kb_id == kb_id)
                )
            ).scalar_one_or_none()
        return True  # 异常深的链按环处理，如实拒

    async def update_folder(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        folder_id: int,
        *,
        name: str | None = None,
        parent_id: int | None = None,
        move_to_root: bool = False,
    ) -> dict[str, Any]:
        folder = await self._get_folder(db, resource_owner_id, folder_id)
        new_parent_id = folder.parent_id
        if move_to_root:
            new_parent_id = None
        elif parent_id is not None:
            if parent_id == folder.id:
                raise errors.RequestError(msg='不能移动到自身')
            parent = await self._get_folder(db, resource_owner_id, parent_id)
            if parent.kb_id != folder.kb_id:
                raise errors.RequestError(msg='目标目录不属于同一知识库')
            if await self._is_descendant(db, folder.kb_id, parent_id, folder.id):
                raise errors.RequestError(msg='不能移动到自己的子目录下')
            new_parent_id = parent_id
        new_name = name if name is not None else folder.name
        await self._assert_sibling_name_free(db, folder.kb_id, new_parent_id, new_name, exclude_id=folder.id)
        folder.name = new_name
        folder.parent_id = new_parent_id
        folder.updated_time = timezone.now()
        await db.flush()
        return _folder_dict(folder)

    async def delete_folder(self, db: AsyncSession, resource_owner_id: str, folder_id: int) -> None:
        folder = await self._get_folder(db, resource_owner_id, folder_id)
        has_child = (
            await db.execute(
                select(Folder.id)
                .where(Folder.parent_id == folder.id, Folder.deleted_time.is_(None))
                .limit(1)
            )
        ).first()
        has_doc = (
            await db.execute(
                select(Document.id)
                .where(Document.folder_id == folder.id, Document.deleted_time.is_(None))
                .limit(1)
            )
        ).first()
        if has_child or has_doc:
            raise errors.ConflictError(msg='目录非空，无法删除')
        folder.deleted_time = timezone.now()

    # ---------- documents ----------

    async def _get_document(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> Document:
        doc = (
            await db.execute(
                select(Document).where(
                    Document.id == doc_id, Document.owner_id == resource_owner_id, Document.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise errors.NotFoundError(msg='文档不存在')
        return doc

    async def _validate_folder_for_kb(
        self, db: AsyncSession, resource_owner_id: str, kb_id: int, folder_id: int | None
    ) -> int | None:
        if folder_id is None:
            return None
        folder = await self._get_folder(db, resource_owner_id, folder_id)
        if folder.kb_id != kb_id:
            raise errors.RequestError(msg='目录不属于该知识库')
        return folder_id

    async def list_documents(
        self, db: AsyncSession, resource_owner_id: str, kb_id: int, *, folder_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列文档；folder_id=None 全库、=0 库根、>0 指定目录。触发 parse_status 读时对账（best-effort）。"""
        kb = await self._get_kb(db, resource_owner_id, kb_id)
        await self._reconcile_parse_status(db, kb)
        stmt = select(Document).where(Document.kb_id == kb_id, Document.deleted_time.is_(None))
        if folder_id == ROOT_FOLDER_SENTINEL:
            stmt = stmt.where(Document.folder_id.is_(None))
        elif folder_id is not None:
            stmt = stmt.where(Document.folder_id == folder_id)
        rows = (await db.execute(stmt.order_by(Document.updated_time.desc().nulls_last(), Document.id.desc()))).scalars()
        return [_document_dict(d) for d in rows]

    async def _reconcile_parse_status(self, db: AsyncSession, kb: Kb) -> None:
        """§4.4 读时对账：对 parsing 文档批量查 RAGFlow 状态并回写；引擎不可达保持现状（不造假）。"""
        pending = (
            (
                await db.execute(
                    select(Document).where(
                        Document.kb_id == kb.id,
                        Document.parse_status == 'parsing',
                        Document.ragflow_document_id.is_not(None),
                        Document.deleted_time.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not pending:
            await self._refresh_kb_counts(db, kb)
            return
        try:
            client, _ = await resolve_knowledge_instance(db)
            remote_docs = await client.list_documents(dataset_id=kb.ragflow_dataset_id, page_size=100)
        except KnowledgeProviderError:
            return  # 引擎不可达：状态保持 parsing，不冒充完成/失败
        remote_by_id = {str(d.get('id')): d for d in remote_docs}
        for doc in pending:
            remote = remote_by_id.get(str(doc.ragflow_document_id))
            if remote is None:
                continue
            run = str(remote.get('run', '')).upper()
            if run in ('DONE', '3'):
                doc.parse_status = 'parsed'
                doc.parse_error = None
                doc.chunk_count = int(remote.get('chunk_count') or 0)
            elif run in ('FAIL', '4'):
                doc.parse_status = 'failed'
                doc.parse_error = str(remote.get('progress_msg') or '索引失败')[:500]
            doc.updated_time = timezone.now()
        await self._refresh_kb_counts(db, kb)

    async def _refresh_kb_counts(self, db: AsyncSession, kb: Kb) -> None:
        row = (
            await db.execute(
                select(func.count(Document.id), func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.kb_id == kb.id, Document.deleted_time.is_(None)
                )
            )
        ).one()
        kb.document_count = int(row[0])
        kb.chunk_count = int(row[1])

    async def upload_file_document(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        kb_id: int,
        *,
        filename: str,
        data: bytes,
        mime: str,
        folder_id: int | None = None,
        source: str = 'ui',
        agent_hasn_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """§4.3-A：原件落私有桶（权威，失败=整次失败）→ 落行 → 推引擎副本 → 触发解析。"""
        if not data:
            raise errors.RequestError(msg='文件为空')
        if len(data) > MAX_FILE_SIZE:
            raise errors.RequestError(msg=f'文件超出大小上限 {MAX_FILE_SIZE // (1024 * 1024)}MB')
        kb = await self._get_kb(db, resource_owner_id, kb_id)
        folder_id = await self._validate_folder_for_kb(db, resource_owner_id, kb_id, folder_id)
        stored = await _owner_storage.upload_bytes(
            owner_hasn_id=resource_owner_id,
            data=data,
            filename=filename,
            mime=mime,
            category='private_doc',
            source_app='knowledge',
            idempotency_key=f'knowledge:{idempotency_key or uuid.uuid4().hex}',
            extract_status='done',
        )
        doc = Document(
            kb_id=kb_id,
            folder_id=folder_id,
            owner_id=resource_owner_id,
            kind='file',
            name=filename,
            size_bytes=len(data),
            mime_type=mime,
            content=None,
            asset_uri=stored.uri,
            current_version=0,
            ragflow_document_id=None,
            parse_status='uploading',
            parse_error=None,
            chunk_count=0,
            source=source,
            agent_hasn_id=agent_hasn_id,
        )
        db.add(doc)
        await db.flush()
        await _owner_storage.bind_asset_in_transaction(
            db,
            owner_hasn_id=resource_owner_id,
            asset_id=stored.asset_id,
            resource_uri=f'hasn://knowledge/documents/{doc.id}',
            role='source',
        )
        await self._push_copy_to_engine(db, kb, doc, filename=filename, data=data, mime=mime)
        await self._refresh_kb_counts(db, kb)
        return _document_dict(doc)

    @staticmethod
    def _asset_filename(title: str, mime: str | None) -> str:
        """资产入库文件名：title 已带扩展名则用之；否则按 MIME 猜扩展名补全（让引擎按类型解析）。"""
        name = (title or 'untitled').strip() or 'untitled'
        if '.' in name.rsplit('/', 1)[-1]:
            return name
        ext = mimetypes.guess_extension(mime or '') or ''
        return f'{name}{ext}'

    async def upload_asset_document(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        kb_id: int,
        *,
        asset_uri: str,
        title: str,
        folder_id: int | None = None,
        source: str = 'agent',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """asset_uri 入库：引用已在私有桶的真实文件（agent 生成的图/收到的文件等）→ 取桶字节→建文档副本。

        越权防护：资产必须属于同一主人（asset.owner_hasn_id == resource_owner_id），否则如实拒。
        语义对齐 D10：知识库文档自持其原件副本，源资产不动。
        """
        asset_id = asset_uri.rsplit('/', 1)[-1]
        asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
        if asset is None:
            raise errors.NotFoundError(msg='资产不存在')
        if asset.owner_hasn_id != resource_owner_id:
            raise errors.ForbiddenError(msg='该资产不属于你的主人，无法入库')
        if asset.object_state == 'missing':
            raise errors.NotFoundError(msg='STORAGE_OBJECT_MISSING')
        data = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=asset.object_key)
        filename = self._asset_filename(title, asset.mime)
        return await self.upload_file_document(
            db,
            resource_owner_id,
            kb_id,
            filename=filename,
            data=data,
            mime=asset.mime or 'application/octet-stream',
            folder_id=folder_id,
            source=source,
            agent_hasn_id=agent_hasn_id,
        )

    async def _push_copy_to_engine(
        self, db: AsyncSession, kb: Kb, doc: Document, *, filename: str, data: bytes, mime: str
    ) -> None:
        """推副本给 RAGFlow + 触发解析；失败如实落 failed（原件/正文已安全，可重试索引）。"""
        # 空正文无可索引内容（如刚新建的空白原生文档/被清空的文档）：不推引擎，
        # 否则 RAGFlow 解析空文档必失败 → 误报「索引失败」。删残留副本后置 parsed/0 chunks。
        if not data.strip():
            if doc.ragflow_document_id:
                try:
                    client, _ = await resolve_knowledge_instance(db)
                    await client.delete_documents(
                        dataset_id=kb.ragflow_dataset_id, ids=[doc.ragflow_document_id]
                    )
                except KnowledgeProviderError:
                    pass  # 引擎不可达/副本已不存在：尽力而为，本地状态仍归零
                doc.ragflow_document_id = None
            doc.parse_status = 'parsed'
            doc.parse_error = None
            doc.chunk_count = 0
            doc.updated_time = timezone.now()
            await db.flush()
            return
        try:
            client, _ = await resolve_knowledge_instance(db)
            if doc.ragflow_document_id:
                try:
                    await client.delete_documents(dataset_id=kb.ragflow_dataset_id, ids=[doc.ragflow_document_id])
                except KnowledgeProviderError as exc:
                    if exc.code != 'knowledge_provider_error':
                        raise
                    # 引擎里已不存在旧副本 → 直接重传
            remote = await client.upload_document(
                dataset_id=kb.ragflow_dataset_id, filename=filename, data=data, mime=mime
            )
            doc.ragflow_document_id = str(remote['id'])
            await client.trigger_parse(dataset_id=kb.ragflow_dataset_id, document_ids=[doc.ragflow_document_id])
            doc.parse_status = 'parsing'
            doc.parse_error = None
        except KnowledgeProviderError as exc:
            doc.parse_status = 'failed'
            doc.parse_error = f'{exc.code}: {exc.message}'[:500]
        doc.updated_time = timezone.now()
        await db.flush()

    async def delete_document(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> None:
        """删文档：引擎副本必须删净（避免孤儿向量仍可检索），不可达如实报错。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        kb = await self._get_kb(db, resource_owner_id, doc.kb_id)
        if doc.ragflow_document_id:
            client, _ = await resolve_knowledge_instance(db)
            try:
                await client.delete_documents(dataset_id=kb.ragflow_dataset_id, ids=[doc.ragflow_document_id])
            except KnowledgeProviderError as exc:
                if exc.code != 'knowledge_provider_error':
                    raise
        doc.deleted_time = timezone.now()
        await self._refresh_kb_counts(db, kb)

    async def get_document(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> dict[str, Any]:
        doc = await self._get_document(db, resource_owner_id, doc_id)
        return _document_dict(doc, with_content=doc.kind == 'native')

    async def download_document(
        self, db: AsyncSession, resource_owner_id: str, doc_id: int
    ) -> tuple[str, str, AsyncIterator[bytes]]:
        """下载原文件：私有桶流式（不经 RAGFlow，引擎宕机不影响，D10）。仅 file。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        if doc.kind != 'file' or not doc.asset_uri:
            raise errors.RequestError(msg='原生文档无原始文件，请使用正文接口')
        asset_id = doc.asset_uri.rsplit('/', 1)[-1]
        asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
        if asset is None:
            raise errors.NotFoundError(msg='原件资产不存在')
        if asset.object_state == 'missing':
            raise errors.NotFoundError(msg='STORAGE_OBJECT_MISSING')
        stream = storage_service.read_stream(db, storage_id=asset.storage_id, object_key=asset.object_key)
        return doc.name, doc.mime_type or 'application/octet-stream', stream

    # ---------- native documents（D9）----------

    def _validate_native_content(self, content: str) -> None:
        # 按字符数（Unicode 码点）卡 5000 字上限；超限直接拒绝并引导拆分 + 深链互连，
        # 不静默截断（截断会丢内容），也不自动降级为 file——原生优先原则：native 可编辑、
        # 编辑成本低，长内容应拆成多篇聚焦的 native 文档 + 深链互连，而非落成难改的 file。
        length = len(content)
        if length > MAX_NATIVE_CONTENT_CHARS:
            raise errors.RequestError(
                msg=f'原生文档正文超出 {MAX_NATIVE_CONTENT_CHARS} 字上限（当前 {length} 字）：'
                '请拆成多篇更聚焦的文档，并用深链 hasn://knowledge/documents/{doc_id} 互相关联'
            )

    async def check_document_links(
        self, db: AsyncSession, resource_owner_id: str, kb_id: int, content: str
    ) -> dict[str, Any]:
        """校验正文里的文档深链是否合法（供分身写前预检 / 保存时强校验共用）。

        逐个解析 hasn://knowledge/documents/{doc_id}，判定每条：
        - not_found：目标文档不存在或已删除；
        - cross_kb：目标文档存在但属于**别的**知识库（深链只能指向同一库内文档）；
        - ok：存在、未删、且同库。
        返回 {'valid': 全部 ok, 'total': 去重后链接数, 'invalid_count': 非法数, 'links': [...]}。
        """
        # 先确认 kb 归当前主人（越权/不存在如实抛）——避免拿别人的库当校验上下文。
        await self._get_kb(db, resource_owner_id, kb_id)
        # 去重保序：同一文档被引用多次只校验一次，但按首次出现顺序回报。
        ref_ids: list[int] = []
        seen: set[int] = set()
        for m in _DOC_LINK_RE.finditer(content or ''):
            doc_id = int(m.group(1))
            if doc_id not in seen:
                seen.add(doc_id)
                ref_ids.append(doc_id)
        if not ref_ids:
            return {'valid': True, 'total': 0, 'invalid_count': 0, 'links': []}
        # 一次批量取回被引用文档的 (id, kb_id, name)（只取未删除的）。
        rows = (
            await db.execute(
                select(Document.id, Document.kb_id, Document.name).where(
                    Document.id.in_(ref_ids), Document.deleted_time.is_(None)
                )
            )
        ).all()
        found: dict[int, tuple[int, str]] = {r[0]: (r[1], r[2]) for r in rows}
        links: list[dict[str, Any]] = []
        invalid = 0
        for doc_id in ref_ids:
            uri = f'hasn://knowledge/documents/{doc_id}'
            hit = found.get(doc_id)
            if hit is None:
                invalid += 1
                links.append({'doc_id': doc_id, 'uri': uri, 'ok': False, 'reason': 'not_found', 'title': None})
            elif hit[0] != kb_id:
                invalid += 1
                links.append({'doc_id': doc_id, 'uri': uri, 'ok': False, 'reason': 'cross_kb', 'title': hit[1]})
            else:
                links.append({'doc_id': doc_id, 'uri': uri, 'ok': True, 'reason': None, 'title': hit[1]})
        return {'valid': invalid == 0, 'total': len(ref_ids), 'invalid_count': invalid, 'links': links}

    async def _assert_doc_links_valid(
        self, db: AsyncSession, resource_owner_id: str, kb_id: int, content: str | None
    ) -> None:
        """保存原生文档时强校验深链合法性：有非法链接即整条拒绝，不落库。

        「不能链接到不存在的文档或其它知识库的文档」——福仔明确要求保存时也要校验。
        """
        if not content:
            return
        result = await self.check_document_links(db, resource_owner_id, kb_id, content)
        if result['valid']:
            return
        bad = [lk for lk in result['links'] if not lk['ok']]
        reason_zh = {'not_found': '不存在/已删除', 'cross_kb': '属于其它知识库'}
        detail = '；'.join(f'{lk["uri"]}（{reason_zh.get(lk["reason"], lk["reason"])}）' for lk in bad)
        raise errors.RequestError(
            msg=f'正文含 {len(bad)} 条无效深链，无法保存：{detail}。'
            '深链只能指向同一知识库内已存在的文档，请先建好目标文档或修正链接'
        )

    async def create_native_document(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        kb_id: int,
        *,
        title: str,
        content: str,
        folder_id: int | None = None,
        source: str = 'ui',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """§4.3-B：正文落 PG（权威）+ 版本 1 → 渲染 .md 推引擎 → 触发解析。"""
        self._validate_native_content(content)
        # 保存时强校验深链：不能指向不存在/已删除或其它库的文档，有非法即整条拒绝、不落库。
        await self._assert_doc_links_valid(db, resource_owner_id, kb_id, content)
        kb = await self._get_kb(db, resource_owner_id, kb_id)
        folder_id = await self._validate_folder_for_kb(db, resource_owner_id, kb_id, folder_id)
        doc = Document(
            kb_id=kb_id,
            folder_id=folder_id,
            owner_id=resource_owner_id,
            kind='native',
            name=title,
            size_bytes=len(content.encode('utf-8')),
            mime_type='text/markdown',
            content=content,
            asset_uri=None,
            current_version=1,
            ragflow_document_id=None,
            parse_status='uploading',
            parse_error=None,
            chunk_count=0,
            source=source,
            agent_hasn_id=agent_hasn_id,
        )
        db.add(doc)
        await db.flush()
        db.add(
            DocumentVersion(
                document_id=doc.id,
                version_no=1,
                title=title,
                content=content,
                source=source,
                agent_hasn_id=agent_hasn_id,
            )
        )
        await self._push_copy_to_engine(
            db, kb, doc, filename=_native_filename(title), data=content.encode('utf-8'), mime='text/markdown'
        )
        await self._refresh_kb_counts(db, kb)
        return _document_dict(doc, with_content=True)

    async def update_native_document(
        self,
        db: AsyncSession,
        resource_owner_id: str,
        doc_id: int,
        *,
        title: str | None = None,
        content: str | None = None,
        folder_id: int | None = None,
        move_to_root: bool = False,
        source: str = 'ui',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """更新原生文档：title/content 变更=落新版本+保存即重向量化（删旧副本重传）；仅移动目录不重索引。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        kb = await self._get_kb(db, resource_owner_id, doc.kb_id)
        if move_to_root:
            doc.folder_id = None
        elif folder_id is not None:
            doc.folder_id = await self._validate_folder_for_kb(db, resource_owner_id, doc.kb_id, folder_id)
        content_changed = False
        if doc.kind == 'native' and (title is not None or content is not None):
            new_title = title if title is not None else doc.name
            new_content = content if content is not None else (doc.content or '')
            if new_title != doc.name or new_content != (doc.content or ''):
                self._validate_native_content(new_content)
                # 保存时强校验深链（同库/存在/未删），有非法即整条拒绝、不落新版本。
                await self._assert_doc_links_valid(db, resource_owner_id, doc.kb_id, new_content)
                doc.name = new_title
                doc.content = new_content
                doc.size_bytes = len(new_content.encode('utf-8'))
                doc.current_version = (doc.current_version or 0) + 1
                db.add(
                    DocumentVersion(
                        document_id=doc.id,
                        version_no=doc.current_version,
                        title=new_title,
                        content=new_content,
                        source=source,
                        agent_hasn_id=agent_hasn_id,
                    )
                )
                content_changed = True
        elif doc.kind == 'file' and title is not None:
            doc.name = title
        doc.updated_time = timezone.now()
        await db.flush()
        if content_changed:
            await self._push_copy_to_engine(
                db, kb, doc, filename=_native_filename(doc.name), data=(doc.content or '').encode('utf-8'),
                mime='text/markdown',
            )
        return _document_dict(doc, with_content=doc.kind == 'native')

    async def get_native_content(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> dict[str, Any]:
        """原生文档正文（PG 权威，不经 RAGFlow）。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        if doc.kind != 'native':
            raise errors.RequestError(msg='非原生文档，请使用下载接口')
        return _document_dict(doc, with_content=True)

    async def list_versions(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> list[dict[str, Any]]:
        doc = await self._get_document(db, resource_owner_id, doc_id)
        rows = (
            await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_no.desc())
            )
        ).scalars()
        return [_version_dict(v) for v in rows]

    async def get_version(
        self, db: AsyncSession, resource_owner_id: str, doc_id: int, version_no: int
    ) -> dict[str, Any]:
        doc = await self._get_document(db, resource_owner_id, doc_id)
        row = (
            await db.execute(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == doc.id, DocumentVersion.version_no == version_no
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='版本不存在')
        return _version_dict(row, with_content=True)

    async def restore_version(
        self, db: AsyncSession, resource_owner_id: str, doc_id: int, version_no: int, *, source: str = 'ui',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """恢复版本 = 以快照落新版本 + 重向量化（版本只增不改）。"""
        snapshot = await self.get_version(db, resource_owner_id, doc_id, version_no)
        return await self.update_native_document(
            db,
            resource_owner_id,
            doc_id,
            title=snapshot['title'],
            content=snapshot['content'],
            source=source,
            agent_hasn_id=agent_hasn_id,
        )

    async def fetch_file_doc_text(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> dict[str, Any]:
        """file 文档解析后文本（引擎分块拼接；fetch_doc 工具用，非二进制）。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        kb = await self._get_kb(db, resource_owner_id, doc.kb_id)
        if not doc.ragflow_document_id or doc.parse_status != 'parsed':
            raise KnowledgeProviderError(
                'knowledge_document_parse_failed', f'文档尚未完成索引（状态：{doc.parse_status}），无解析文本'
            )
        client, _ = await resolve_knowledge_instance(db)
        chunks = await client.list_chunks(dataset_id=kb.ragflow_dataset_id, document_id=doc.ragflow_document_id)
        data = _document_dict(doc)
        data['chunks'] = [c.get('content') for c in chunks]
        return data

    async def reindex_document(self, db: AsyncSession, resource_owner_id: str, doc_id: int) -> dict[str, Any]:
        """重新索引（D10）：file 从私有桶取原件重放；native 以 PG 正文重放——删旧引擎副本重传重解析。"""
        doc = await self._get_document(db, resource_owner_id, doc_id)
        kb = await self._get_kb(db, resource_owner_id, doc.kb_id)
        if doc.kind == 'native':
            data = (doc.content or '').encode('utf-8')
            filename = _native_filename(doc.name)
            mime = 'text/markdown'
        else:
            if not doc.asset_uri:
                raise errors.RequestError(msg='文档缺少原件引用，无法重新索引')
            asset_id = doc.asset_uri.rsplit('/', 1)[-1]
            asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
            if asset is None:
                raise errors.NotFoundError(msg='原件资产不存在')
            if asset.object_state == 'missing':
                raise errors.NotFoundError(msg='STORAGE_OBJECT_MISSING')
            data = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=asset.object_key)
            filename = doc.name
            mime = doc.mime_type or 'application/octet-stream'
        await self._push_copy_to_engine(db, kb, doc, filename=filename, data=data, mime=mime)
        return _document_dict(doc)

    # ---------- 维度②：agent kb grant ----------

    async def get_agent_grant(self, db: AsyncSession, owner_id: str, agent_hasn_id: str) -> dict[str, Any]:
        row = (
            await db.execute(
                select(AgentKbGrant).where(
                    AgentKbGrant.owner_id == owner_id, AgentKbGrant.agent_hasn_id == agent_hasn_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {'agent_hasn_id': agent_hasn_id, 'mode': 'inherit', 'kb_ids': []}
        return {'agent_hasn_id': row.agent_hasn_id, 'mode': row.mode, 'kb_ids': list(row.kb_ids or [])}

    async def put_agent_grant(
        self, db: AsyncSession, owner_id: str, agent_hasn_id: str, *, mode: str, kb_ids: list[int]
    ) -> dict[str, Any]:
        if mode not in _GRANT_MODES:
            raise errors.RequestError(msg=f'非法授权模式：{mode}')
        if mode == 'restricted' and kb_ids:
            owned = {
                kb['id'] for kb in await self.list_kbs(db, owner_id)
            }
            invalid = [i for i in kb_ids if i not in owned]
            if invalid:
                raise errors.RequestError(msg=f'白名单包含不存在的知识库：{invalid}')
        row = (
            await db.execute(
                select(AgentKbGrant).where(
                    AgentKbGrant.owner_id == owner_id, AgentKbGrant.agent_hasn_id == agent_hasn_id
                )
            )
        ).scalar_one_or_none()
        normalized_ids = kb_ids if mode == 'restricted' else []
        if row is None:
            row = AgentKbGrant(owner_id=owner_id, agent_hasn_id=agent_hasn_id, mode=mode, kb_ids=normalized_ids)
            db.add(row)
        else:
            row.mode = mode
            row.kb_ids = normalized_ids
            row.updated_time = timezone.now()
        await db.flush()
        return {'agent_hasn_id': agent_hasn_id, 'mode': mode, 'kb_ids': normalized_ids}

    async def resolve_retrieval_visible_kbs(self, db: AsyncSession, *, subject: Subject) -> list[Kb]:
        """检索可见集（单点收口 dataset 白名单）：与 `list_accessible_kbs` **同源**
        （拥有 ∪ 共享给我 ∪ 企业可见），再施加两道检索专属闸：

        1. **分身维度② grant** —— 仅裁剪「主人自己的库」(relation=owner)：`denied` 整体拒；
           `restricted` 只保留白名单内的自有库。分享/企业来的库各有其独立 ACL grant（在
           `_accessible_kb_rows` 已按 `_effective_permission` 过滤），**不受**维度② 白名单裁剪
           —— 维度② 白名单（`put_agent_grant` 校验）本就只针对主人自有库。
        2. 只保留 `status='active'` 且已回填 `ragflow_dataset_id` 的库（无 dataset 无法检索）。

        这样「好友把库/文档分享给我 → 我和我的分身都能**检索**到」与浏览走同一份可见集，
        而单账号模型下同一个平台 service key 能读任意 dataset，检索只需把这些库的
        `ragflow_dataset_id` 并进白名单即可（隔离与分享都在应用层单点收口）。
        """
        rows = await self._accessible_kb_rows(db, subject=subject)
        if subject.kind == 'agent':
            grant = await self.get_agent_grant(db, subject.owner_hasn_id, subject.hasn_id)
            if grant['mode'] == 'denied':
                raise KnowledgeProviderError('knowledge_grant_denied', '主人已禁止该分身访问知识库')
            if grant['mode'] == 'restricted':
                allowed = set(grant['kb_ids'])
                # 维度② 白名单只约束自有库（relation=owner）；分享/企业来的库不受此白名单裁剪
                rows = [(kb, perm, rel) for (kb, perm, rel) in rows if rel != 'owner' or kb.id in allowed]
                # 交集空即拒（显式告知分身被拒，而非静默返回空检索）——分享/企业库能救回则不触发
                if not rows:
                    raise KnowledgeProviderError('knowledge_grant_denied', '分身知识库白名单为空（交集为空即拒）')
        return [kb for (kb, _perm, _rel) in rows if kb.status == 'active' and kb.ragflow_dataset_id]

    async def resolve_agent_visible_kbs(
        self, db: AsyncSession, owner_id: str, agent_hasn_id: str
    ) -> list[Kb]:
        """分身可达知识库（维度② grant 裁剪后）。委托统一检索可见集解析——含共享/企业库，
        使 list_datasets / fetch_doc 可达范围与 search 检索可见集完全一致（不漂移）。"""
        return await self.resolve_retrieval_visible_kbs(db, subject=Subject.agent(agent_hasn_id, owner_id))

    # ---------- 检索 ----------

    async def search(
        self,
        db: AsyncSession,
        owner_id: str,
        *,
        question: str,
        kb_ids: list[int] | None = None,
        top_k: int = 8,
        similarity_threshold: float | None = None,
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """检索：service 单点注入「调用者可见 kb 对应的 dataset_ids」（隔离纪律）。

        agent_hasn_id 非空 = Agent 视角（先过维度② grant）；None = owner 本人。
        """
        subject = Subject.agent(agent_hasn_id, owner_id) if agent_hasn_id else Subject.human(owner_id)
        # 可见集 = 拥有 ∪ 共享给我 ∪ 企业可见（agent 再过维度② grant）；与浏览同源，故好友分享的库/文档
        # 也进入检索范围（单账号模型下同一 service key 能读任意 dataset，只需把这些库的 dataset 并进白名单）。
        visible = await self.resolve_retrieval_visible_kbs(db, subject=subject)
        if kb_ids:
            requested = set(kb_ids)
            visible = [kb for kb in visible if kb.id in requested]
            if not visible and agent_hasn_id:
                raise KnowledgeProviderError('knowledge_grant_denied', '请求的知识库不在分身可达范围内')
        if not visible:
            return {'chunks': [], 'total': 0, 'kb_count': 0}
        client, _ = await resolve_knowledge_instance(db)
        dataset_by_id = {kb.ragflow_dataset_id: kb for kb in visible}
        # 韧性：白名单里若混入不再属于当前 RagFlow 账号的孤儿 dataset（如切换实例后遗留的旧库、
        # 或引擎侧被手动删除），RagFlow 对批量 dataset_ids 整批拒绝（code=102 you don't own the
        # dataset X）——逐个识别剔除后重试，避免一个孤儿库拖垮同一 owner 名下其它正常库的检索。
        remaining_ids = list(dataset_by_id.keys())
        data: dict[str, Any] | None = None
        while remaining_ids:
            try:
                data = await client.retrieval(
                    question=question,
                    dataset_ids=remaining_ids,
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )
                break
            except KnowledgeProviderError as exc:
                orphan_id = _extract_unowned_dataset_id(exc.message, remaining_ids)
                if orphan_id is None:
                    raise
                remaining_ids.remove(orphan_id)
        if data is None:
            return {'chunks': [], 'total': 0, 'kb_count': 0}
        raw_chunks = data.get('chunks') or []
        # 纵深防御：即便上游只应返回请求 dataset 内的分块，仍按「本次允许的 dataset 白名单」再过滤一遍，
        # 杜绝引擎越权把其它 dataset 的分块泄漏给调用者——隔离不单靠上游遵约（应用层兜底）。
        allowed_dataset_ids = set(remaining_ids)
        raw_chunks = [c for c in raw_chunks if str(c.get('dataset_id')) in allowed_dataset_ids]
        rf_doc_ids = {str(c.get('document_id')) for c in raw_chunks if c.get('document_id')}
        doc_rows: dict[str, Document] = {}
        if rf_doc_ids:
            # 文档元数据按「可见库」定界（不用 owner_id）：共享库里的文档归好友所有，用 owner_id 会漏掉其元数据；
            # visible 已在上面经 ACL 收窄，据其 kb_id 取文档既正确又安全。
            visible_kb_ids = [kb.id for kb in visible]
            for row in (
                (
                    await db.execute(
                        select(Document).where(
                            Document.ragflow_document_id.in_(rf_doc_ids),
                            Document.kb_id.in_(visible_kb_ids),
                            Document.parse_status == 'parsed',
                            Document.deleted_time.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                doc_rows[str(row.ragflow_document_id)] = row
        raw_chunks = [
            chunk
            for chunk in raw_chunks
            if str(chunk.get('document_id')) in doc_rows
        ]
        chunks: list[dict[str, Any]] = []
        for c in raw_chunks:
            doc = doc_rows.get(str(c.get('document_id')))
            kb = dataset_by_id.get(str(c.get('dataset_id')))
            chunks.append(
                {
                    'content': c.get('content'),
                    'similarity': c.get('similarity'),
                    'kb_id': kb.id if kb else None,
                    'kb_name': kb.name if kb else None,
                    'document_id': doc.id if doc else None,
                    # 片段所属文档的深链（云端权威 id）：分身检索到片段不全时，凭它调 fetch_doc 取整篇，
                    # 或在产出里引用为可点跳转链接。检索命中引擎切块 → 这里回填 PG 权威文档。
                    'document_uri': (f'hasn://knowledge/documents/{doc.id}' if doc else None),
                    'document_name': doc.name if doc else c.get('document_keyword'),
                    'document_kind': doc.kind if doc else None,
                    'folder_id': doc.folder_id if doc else None,
                    'source': doc.source if doc else None,
                    'agent_hasn_id': doc.agent_hasn_id if doc else None,
                }
            )
        return {'chunks': chunks, 'total': int(data.get('total') or len(chunks)), 'kb_count': len(visible)}

    # ---------- 审计 ----------

    async def write_ui_audit(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        user_id: int | None,
        method: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """UI 写操作落域审计（与 App 工具审计共表，actor_type=owner 区分）。"""
        db.add(
            HasnAiNativeAppAudit(
                trace_id=f'kui_{uuid.uuid4().hex[:24]}',
                step='knowledge_ui',
                workspace_kind='personal',
                user_id=user_id,
                enterprise_id=None,
                app_id='knowledge',
                app_version=None,
                actor_type='owner',
                agent_hasn_id=None,
                owner_hasn_id=owner_id,
                session_uuid=None,
                method=method,
                capability_id=None,
                tool_id=None,
                event_type=None,
                required_scopes=[],
                agent_scopes_snapshot=[],
                workspace_role='owner',
                risk_level=None,
                decision='allow',
                confirmation_id=None,
                result_ref=None,
                error_code=None,
                context=context or {},
            )
        )

    async def list_audit(
        self, db: AsyncSession, owner_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """合并审计视图：App 工具审计 + UI 域审计（共表，app_id=knowledge，owner 隔离）。"""
        rows = (
            await db.execute(
                select(HasnAiNativeAppAudit)
                .where(
                    HasnAiNativeAppAudit.app_id == 'knowledge',
                    HasnAiNativeAppAudit.owner_hasn_id == owner_id,
                )
                .order_by(HasnAiNativeAppAudit.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return [
            {
                'id': r.id,
                'trace_id': r.trace_id,
                'actor_type': r.actor_type,
                'agent_hasn_id': r.agent_hasn_id,
                'method': r.method,
                'tool_id': r.tool_id,
                'decision': r.decision,
                'error_code': r.error_code,
                'context': r.context,
                'created_at': r.created_at,
            }
            for r in rows
        ]


knowledge_service = KnowledgeService()
