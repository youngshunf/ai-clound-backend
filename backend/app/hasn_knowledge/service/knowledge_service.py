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
import uuid

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy import func, select

from backend.app.hasn.model.hasn_ai_native_app_audit import HasnAiNativeAppAudit
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_knowledge.model import AgentKbGrant, Document, DocumentVersion, Folder, Kb
from backend.app.hasn_knowledge.service.instance import resolve_knowledge_instance
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError, RAGFlowClient
from backend.common.exception import errors
from backend.plugin.s3.service.storage_service import storage_service
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# 文件上传上限（对齐 hasn_assets file 限额）
MAX_FILE_SIZE = 50 * 1024 * 1024
# 原生文档正文上限（1MB Markdown，防滥用）
MAX_NATIVE_CONTENT_BYTES = 1 * 1024 * 1024
# folder_id 查询参数的「库根」哨兵（真实 id 从 1 起）
ROOT_FOLDER_SENTINEL = 0

_GRANT_MODES = ('inherit', 'restricted', 'denied')


def _kb_dict(kb: Kb) -> dict[str, Any]:
    return {
        'id': kb.id,
        'name': kb.name,
        'description': kb.description,
        'scope': kb.scope,
        'embedding_model': kb.embedding_model,
        'document_count': kb.document_count,
        'chunk_count': kb.chunk_count,
        'status': kb.status,
        'created_time': kb.created_time,
        'updated_time': kb.updated_time,
    }


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


class KnowledgeService:
    # ---------- kb ----------

    async def _get_kb(self, db: AsyncSession, owner_id: str, kb_id: int) -> Kb:
        kb = (
            await db.execute(
                select(Kb).where(Kb.id == kb_id, Kb.owner_id == owner_id, Kb.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if kb is None:
            raise errors.NotFoundError(msg='知识库不存在')
        return kb

    async def list_kbs(self, db: AsyncSession, owner_id: str) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(Kb).where(Kb.owner_id == owner_id, Kb.deleted_time.is_(None)).order_by(Kb.id.desc())
            )
        ).scalars()
        return [_kb_dict(kb) for kb in rows]

    async def create_kb(self, db: AsyncSession, owner_id: str, *, name: str, description: str | None) -> dict[str, Any]:
        """建库：先建 RAGFlow dataset（失败则不落 kb 行，如实报错），成功后落域行。"""
        client, config = await resolve_knowledge_instance(db)
        # dataset 内部名取唯一随机串（单平台租户下避免撞名；展示名只活在域行）
        dataset = await client.create_dataset(name=f'hxkb_{uuid.uuid4().hex[:16]}', embedding_model=config.default_embd_id)
        kb = Kb(
            owner_id=owner_id,
            scope='personal',
            enterprise_id=None,
            name=name,
            description=description,
            ragflow_dataset_id=str(dataset['id']),
            embedding_model=config.default_embd_id,
            document_count=0,
            chunk_count=0,
            status='active',
        )
        db.add(kb)
        await db.flush()
        return _kb_dict(kb)

    async def delete_kb(self, db: AsyncSession, owner_id: str, kb_id: int) -> None:
        """删库：级联删 RAGFlow dataset + 文档/目录行。引擎不可达如实报错（避免残留孤儿向量可检索）。"""
        kb = await self._get_kb(db, owner_id, kb_id)
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

    async def _get_folder(self, db: AsyncSession, owner_id: str, folder_id: int) -> Folder:
        folder = (
            await db.execute(
                select(Folder).where(
                    Folder.id == folder_id, Folder.owner_id == owner_id, Folder.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if folder is None:
            raise errors.NotFoundError(msg='目录不存在')
        return folder

    async def get_folder(self, db: AsyncSession, owner_id: str, folder_id: int) -> dict[str, Any]:
        """读单个目录（owner 隔离）；供 agent 面按 folder_id 反查所属 kb 做可达性闸门。"""
        return _folder_dict(await self._get_folder(db, owner_id, folder_id))

    async def list_folders(self, db: AsyncSession, owner_id: str, kb_id: int) -> list[dict[str, Any]]:
        await self._get_kb(db, owner_id, kb_id)
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
        self, db: AsyncSession, owner_id: str, kb_id: int, *, name: str, parent_id: int | None = None
    ) -> dict[str, Any]:
        await self._get_kb(db, owner_id, kb_id)
        if parent_id is not None:
            parent = await self._get_folder(db, owner_id, parent_id)
            if parent.kb_id != kb_id:
                raise errors.RequestError(msg='父目录不属于该知识库')
        await self._assert_sibling_name_free(db, kb_id, parent_id, name)
        folder = Folder(kb_id=kb_id, owner_id=owner_id, parent_id=parent_id, name=name, sort_order=0)
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
        owner_id: str,
        folder_id: int,
        *,
        name: str | None = None,
        parent_id: int | None = None,
        move_to_root: bool = False,
    ) -> dict[str, Any]:
        folder = await self._get_folder(db, owner_id, folder_id)
        new_parent_id = folder.parent_id
        if move_to_root:
            new_parent_id = None
        elif parent_id is not None:
            if parent_id == folder.id:
                raise errors.RequestError(msg='不能移动到自身')
            parent = await self._get_folder(db, owner_id, parent_id)
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

    async def delete_folder(self, db: AsyncSession, owner_id: str, folder_id: int) -> None:
        folder = await self._get_folder(db, owner_id, folder_id)
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

    async def _get_document(self, db: AsyncSession, owner_id: str, doc_id: int) -> Document:
        doc = (
            await db.execute(
                select(Document).where(
                    Document.id == doc_id, Document.owner_id == owner_id, Document.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise errors.NotFoundError(msg='文档不存在')
        return doc

    async def _validate_folder_for_kb(
        self, db: AsyncSession, owner_id: str, kb_id: int, folder_id: int | None
    ) -> int | None:
        if folder_id is None:
            return None
        folder = await self._get_folder(db, owner_id, folder_id)
        if folder.kb_id != kb_id:
            raise errors.RequestError(msg='目录不属于该知识库')
        return folder_id

    async def list_documents(
        self, db: AsyncSession, owner_id: str, kb_id: int, *, folder_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列文档；folder_id=None 全库、=0 库根、>0 指定目录。触发 parse_status 读时对账（best-effort）。"""
        kb = await self._get_kb(db, owner_id, kb_id)
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
        owner_id: str,
        kb_id: int,
        *,
        filename: str,
        data: bytes,
        mime: str,
        folder_id: int | None = None,
        source: str = 'ui',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """§4.3-A：原件落私有桶（权威，失败=整次失败）→ 落行 → 推引擎副本 → 触发解析。"""
        if not data:
            raise errors.RequestError(msg='文件为空')
        if len(data) > MAX_FILE_SIZE:
            raise errors.RequestError(msg=f'文件超出大小上限 {MAX_FILE_SIZE // (1024 * 1024)}MB')
        kb = await self._get_kb(db, owner_id, kb_id)
        folder_id = await self._validate_folder_for_kb(db, owner_id, kb_id, folder_id)
        ref = await storage_service.upload(db, data, category='private_doc', filename=filename, content_type=mime)
        asset = await hasn_asset_service.register_asset(
            db, owner_hasn_id=owner_id, ref=ref, kind='file', extract_status='done'
        )
        doc = Document(
            kb_id=kb_id,
            folder_id=folder_id,
            owner_id=owner_id,
            kind='file',
            name=filename,
            size_bytes=len(data),
            mime_type=mime,
            content=None,
            asset_uri=f'hasn://asset/{asset.asset_id}',
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
        owner_id: str,
        kb_id: int,
        *,
        asset_uri: str,
        title: str,
        folder_id: int | None = None,
        source: str = 'agent',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """asset_uri 入库：引用已在私有桶的真实文件（agent 生成的图/收到的文件等）→ 取桶字节→建文档副本。

        越权防护：资产必须属于同一主人（asset.owner_hasn_id == owner_id），否则如实拒。
        语义对齐 D10：知识库文档自持其原件副本，源资产不动。
        """
        asset_id = asset_uri.rsplit('/', 1)[-1]
        asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
        if asset is None:
            raise errors.NotFoundError(msg='资产不存在')
        if asset.owner_hasn_id != owner_id:
            raise errors.ForbiddenError(msg='该资产不属于你的主人，无法入库')
        data = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=asset.object_key)
        filename = self._asset_filename(title, asset.mime)
        return await self.upload_file_document(
            db,
            owner_id,
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

    async def delete_document(self, db: AsyncSession, owner_id: str, doc_id: int) -> None:
        """删文档：引擎副本必须删净（避免孤儿向量仍可检索），不可达如实报错。"""
        doc = await self._get_document(db, owner_id, doc_id)
        kb = await self._get_kb(db, owner_id, doc.kb_id)
        if doc.ragflow_document_id:
            client, _ = await resolve_knowledge_instance(db)
            try:
                await client.delete_documents(dataset_id=kb.ragflow_dataset_id, ids=[doc.ragflow_document_id])
            except KnowledgeProviderError as exc:
                if exc.code != 'knowledge_provider_error':
                    raise
        doc.deleted_time = timezone.now()
        await self._refresh_kb_counts(db, kb)

    async def get_document(self, db: AsyncSession, owner_id: str, doc_id: int) -> dict[str, Any]:
        doc = await self._get_document(db, owner_id, doc_id)
        return _document_dict(doc, with_content=doc.kind == 'native')

    async def download_document(
        self, db: AsyncSession, owner_id: str, doc_id: int
    ) -> tuple[str, str, AsyncIterator[bytes]]:
        """下载原文件：私有桶流式（不经 RAGFlow，引擎宕机不影响，D10）。仅 file。"""
        doc = await self._get_document(db, owner_id, doc_id)
        if doc.kind != 'file' or not doc.asset_uri:
            raise errors.RequestError(msg='原生文档无原始文件，请使用正文接口')
        asset_id = doc.asset_uri.rsplit('/', 1)[-1]
        asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
        if asset is None:
            raise errors.NotFoundError(msg='原件资产不存在')
        stream = storage_service.read_stream(db, storage_id=asset.storage_id, object_key=asset.object_key)
        return doc.name, doc.mime_type or 'application/octet-stream', stream

    # ---------- native documents（D9）----------

    def _validate_native_content(self, content: str) -> None:
        if len(content.encode('utf-8')) > MAX_NATIVE_CONTENT_BYTES:
            raise errors.RequestError(msg='正文超出大小上限 1MB')

    async def create_native_document(
        self,
        db: AsyncSession,
        owner_id: str,
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
        kb = await self._get_kb(db, owner_id, kb_id)
        folder_id = await self._validate_folder_for_kb(db, owner_id, kb_id, folder_id)
        doc = Document(
            kb_id=kb_id,
            folder_id=folder_id,
            owner_id=owner_id,
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
        owner_id: str,
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
        doc = await self._get_document(db, owner_id, doc_id)
        kb = await self._get_kb(db, owner_id, doc.kb_id)
        if move_to_root:
            doc.folder_id = None
        elif folder_id is not None:
            doc.folder_id = await self._validate_folder_for_kb(db, owner_id, doc.kb_id, folder_id)
        content_changed = False
        if doc.kind == 'native' and (title is not None or content is not None):
            new_title = title if title is not None else doc.name
            new_content = content if content is not None else (doc.content or '')
            if new_title != doc.name or new_content != (doc.content or ''):
                self._validate_native_content(new_content)
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

    async def get_native_content(self, db: AsyncSession, owner_id: str, doc_id: int) -> dict[str, Any]:
        """原生文档正文（PG 权威，不经 RAGFlow）。"""
        doc = await self._get_document(db, owner_id, doc_id)
        if doc.kind != 'native':
            raise errors.RequestError(msg='非原生文档，请使用下载接口')
        return _document_dict(doc, with_content=True)

    async def list_versions(self, db: AsyncSession, owner_id: str, doc_id: int) -> list[dict[str, Any]]:
        doc = await self._get_document(db, owner_id, doc_id)
        rows = (
            await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_no.desc())
            )
        ).scalars()
        return [_version_dict(v) for v in rows]

    async def get_version(self, db: AsyncSession, owner_id: str, doc_id: int, version_no: int) -> dict[str, Any]:
        doc = await self._get_document(db, owner_id, doc_id)
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
        self, db: AsyncSession, owner_id: str, doc_id: int, version_no: int, *, source: str = 'ui',
        agent_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """恢复版本 = 以快照落新版本 + 重向量化（版本只增不改）。"""
        snapshot = await self.get_version(db, owner_id, doc_id, version_no)
        return await self.update_native_document(
            db,
            owner_id,
            doc_id,
            title=snapshot['title'],
            content=snapshot['content'],
            source=source,
            agent_hasn_id=agent_hasn_id,
        )

    async def fetch_file_doc_text(self, db: AsyncSession, owner_id: str, doc_id: int) -> dict[str, Any]:
        """file 文档解析后文本（引擎分块拼接；fetch_doc 工具用，非二进制）。"""
        doc = await self._get_document(db, owner_id, doc_id)
        kb = await self._get_kb(db, owner_id, doc.kb_id)
        if not doc.ragflow_document_id or doc.parse_status != 'parsed':
            raise KnowledgeProviderError(
                'knowledge_document_parse_failed', f'文档尚未完成索引（状态：{doc.parse_status}），无解析文本'
            )
        client, _ = await resolve_knowledge_instance(db)
        chunks = await client.list_chunks(dataset_id=kb.ragflow_dataset_id, document_id=doc.ragflow_document_id)
        data = _document_dict(doc)
        data['chunks'] = [c.get('content') for c in chunks]
        return data

    async def reindex_document(self, db: AsyncSession, owner_id: str, doc_id: int) -> dict[str, Any]:
        """重新索引（D10）：file 从私有桶取原件重放；native 以 PG 正文重放——删旧引擎副本重传重解析。"""
        doc = await self._get_document(db, owner_id, doc_id)
        kb = await self._get_kb(db, owner_id, doc.kb_id)
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

    async def resolve_agent_visible_kbs(
        self, db: AsyncSession, owner_id: str, agent_hasn_id: str
    ) -> list[Kb]:
        """维度② 强制：denied 拒；restricted 裁剪白名单；无行/inherit 继承 owner 全部库。"""
        grant = await self.get_agent_grant(db, owner_id, agent_hasn_id)
        if grant['mode'] == 'denied':
            raise KnowledgeProviderError('knowledge_grant_denied', '主人已禁止该分身访问知识库')
        rows = (
            (
                await db.execute(
                    select(Kb).where(Kb.owner_id == owner_id, Kb.deleted_time.is_(None), Kb.status == 'active')
                )
            )
            .scalars()
            .all()
        )
        if grant['mode'] == 'restricted':
            allowed = set(grant['kb_ids'])
            rows = [kb for kb in rows if kb.id in allowed]
            if not rows:
                raise KnowledgeProviderError('knowledge_grant_denied', '分身知识库白名单为空（交集为空即拒）')
        return list(rows)

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
        if agent_hasn_id:
            visible = await self.resolve_agent_visible_kbs(db, owner_id, agent_hasn_id)
        else:
            visible = (
                (
                    await db.execute(
                        select(Kb).where(Kb.owner_id == owner_id, Kb.deleted_time.is_(None), Kb.status == 'active')
                    )
                )
                .scalars()
                .all()
            )
        if kb_ids:
            requested = set(kb_ids)
            visible = [kb for kb in visible if kb.id in requested]
            if not visible and agent_hasn_id:
                raise KnowledgeProviderError('knowledge_grant_denied', '请求的知识库不在分身可达范围内')
        if not visible:
            return {'chunks': [], 'total': 0, 'kb_count': 0}
        client, _ = await resolve_knowledge_instance(db)
        dataset_by_id = {kb.ragflow_dataset_id: kb for kb in visible}
        data = await client.retrieval(
            question=question,
            dataset_ids=list(dataset_by_id.keys()),
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
        raw_chunks = data.get('chunks') or []
        rf_doc_ids = {str(c.get('document_id')) for c in raw_chunks if c.get('document_id')}
        doc_rows: dict[str, Document] = {}
        if rf_doc_ids:
            for row in (
                (
                    await db.execute(
                        select(Document).where(
                            Document.ragflow_document_id.in_(rf_doc_ids),
                            Document.owner_id == owner_id,
                            Document.deleted_time.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                doc_rows[str(row.ragflow_document_id)] = row
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
