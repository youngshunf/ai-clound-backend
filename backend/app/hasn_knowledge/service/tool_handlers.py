"""knowledge App 工具 handlers（gateway_internal，注册于 ai_native_runtime_gateway internal_handlers）。

签名约定：async (db, agent: AgentTokenPayload, input_payload: dict) -> dict。
- 维度①（scope 三态）由云端 Gateway 单点强制，handler 不重复实现；
- 维度②（kb 白名单）+ 行级 owner 隔离在此/或 service 内强制；
- Agent 身份恒取自 JWT claims（agent.agent_hasn_id / agent.owner_hasn_id），绝不读 payload 身份字段。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.3/§2.4/§3。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_knowledge.service.error_adapter import to_http_error
from backend.app.hasn_knowledge.service.knowledge_service import MAX_NATIVE_CONTENT_BYTES, knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


async def handle_knowledge_search(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.search：检索主人可见知识库（维度② 在 service 内裁剪）。"""
    kb_ids_raw = input_payload.get('kb_ids')
    kb_ids = [int(i) for i in kb_ids_raw] if kb_ids_raw else None
    try:
        return await knowledge_service.search(
            db,
            agent.owner_hasn_id,
            question=str(input_payload['query']),
            kb_ids=kb_ids,
            top_k=min(int(input_payload.get('limit') or 8), 50),
            similarity_threshold=input_payload.get('similarity_threshold'),
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc


async def handle_knowledge_list_datasets(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_datasets：列出分身可达的知识库（维度② 裁剪后）。"""
    try:
        kbs = await knowledge_service.resolve_agent_visible_kbs(db, agent.owner_hasn_id, agent.agent_hasn_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {
        'datasets': [
            {
                'id': kb.id,
                'name': kb.name,
                'description': kb.description,
                'document_count': kb.document_count,
                'chunk_count': kb.chunk_count,
            }
            for kb in kbs
        ]
    }


async def _assert_kb_reachable(db: AsyncSession, agent: AgentTokenPayload, kb_id: int) -> None:
    try:
        visible = await knowledge_service.resolve_agent_visible_kbs(db, agent.owner_hasn_id, agent.agent_hasn_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    if kb_id not in {kb.id for kb in visible}:
        raise errors.ForbiddenError(msg='knowledge_grant_denied: 该知识库不在分身可达范围内')


async def handle_knowledge_fetch_doc(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.fetch_doc：返回解析后文本（native=PG 正文；file=引擎分块文本），非二进制。"""
    doc_id = int(input_payload['doc_id'])
    doc = await knowledge_service.get_document(db, agent.owner_hasn_id, doc_id)
    await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    if doc['kind'] == 'native':
        return doc
    try:
        full = await knowledge_service.fetch_file_doc_text(db, agent.owner_hasn_id, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return full


async def handle_knowledge_upload_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.upload_document：content_text(纯文本) 或 asset_uri(已在私有桶的真实文件) 二选一上传并索引。

    - content_text（纯文本内容）→ **优先落原生文档**（可编辑/有版本/Markdown 渲染/在线预览最好/正文逐字保留，
      且同样推进引擎索引可检索——对文本是纯升级）；仅当正文超原生 1MB 上限时，才诚实回落 file 文档
      （引擎切块是唯一能承载超大文本的方式）。
    - asset_uri(hasn://asset/...) → 取桶字节建 file 文档副本（真实二进制文件；资产须属同一主人，越权如实拒）。
    """
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    title = str(input_payload['title']).strip() or 'untitled'
    folder_id = input_payload.get('folder_id')
    folder_id_int = int(folder_id) if folder_id is not None else None
    content_text = input_payload.get('content_text')
    asset_uri = input_payload.get('asset_uri')
    if bool(content_text) == bool(asset_uri):
        raise errors.RequestError(msg='content_text 与 asset_uri 必须二选一')
    try:
        if asset_uri:
            return await knowledge_service.upload_asset_document(
                db,
                agent.owner_hasn_id,
                kb_id,
                asset_uri=str(asset_uri),
                title=title,
                folder_id=folder_id_int,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
        # 纯文本内容优先落原生文档；仅超原生 1MB 上限才回落 file（引擎切块承载超大文本）。
        text = str(content_text)
        if len(text.encode('utf-8')) <= MAX_NATIVE_CONTENT_BYTES:
            result = await knowledge_service.create_native_document(
                db,
                agent.owner_hasn_id,
                kb_id,
                title=title,
                content=text,
                folder_id=folder_id_int,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
            # 不回显整段正文（避免灌爆分身上下文），与 write_doc 出参对齐。
            result.pop('content', None)
            return result
        filename = title if '.' in title.rsplit('/', 1)[-1] else f'{title}.txt'
        return await knowledge_service.upload_file_document(
            db,
            agent.owner_hasn_id,
            kb_id,
            filename=filename,
            data=text.encode('utf-8'),
            mime='text/plain',
            folder_id=folder_id_int,
            source='agent',
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc


# ---------- 知识库（kb）+ 文档：分身替主人维护库与文档（建库/删库/列文档/删文档）----------


async def handle_knowledge_create_kb(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.create_kb：替主人新建知识库（库归主人 owner_hasn_id 所有；inherit 默认即可达）。"""
    name = str(input_payload['name']).strip()
    if not name:
        raise errors.RequestError(msg='知识库名称不能为空')
    description = input_payload.get('description')
    try:
        return await knowledge_service.create_kb(
            db,
            agent.owner_hasn_id,
            name=name,
            description=str(description).strip() if description else None,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc


async def handle_knowledge_delete_kb(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_kb：删主人的整库（级联删文档/目录）；可达性闸门后删。"""
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    try:
        await knowledge_service.delete_kb(db, agent.owner_hasn_id, kb_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {'deleted': True, 'kb_id': kb_id}


async def handle_knowledge_list_documents(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_documents：列某可达知识库的文档（folder_id 省略=全库 / 0=库根 / >0=指定目录）。"""
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    folder_id = input_payload.get('folder_id')
    docs = await knowledge_service.list_documents(
        db,
        agent.owner_hasn_id,
        kb_id,
        folder_id=int(folder_id) if folder_id is not None else None,
    )
    return {'documents': docs}


async def handle_knowledge_delete_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_document：删主人知识库中的一篇文档（按 doc_id 反查所属 kb 做可达性闸门）。"""
    doc_id = int(input_payload['doc_id'])
    doc = await knowledge_service.get_document(db, agent.owner_hasn_id, doc_id)
    await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    try:
        await knowledge_service.delete_document(db, agent.owner_hasn_id, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {'deleted': True, 'doc_id': doc_id}


async def handle_knowledge_move_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.move_document：把一篇已存在文档移进目录 / 移回库根（native 与 file 均可）。

    只改文档所属目录，不改标题/正文、不重建索引；`folder_id`（移进该目录）与 `move_to_root`（移回库根）
    二选一必须给其一。按 doc_id 反查所属 kb 做可达性闸门（维度②越权如实拒）。
    """
    doc_id = int(input_payload['doc_id'])
    doc = await knowledge_service.get_document(db, agent.owner_hasn_id, doc_id)
    await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    folder_id = input_payload.get('folder_id')
    move_to_root = bool(input_payload.get('move_to_root'))
    if folder_id is None and not move_to_root:
        raise errors.RequestError(msg='folder_id 与 move_to_root 必须提供其一（移进目录或移回库根）')
    try:
        result = await knowledge_service.update_native_document(
            db,
            agent.owner_hasn_id,
            doc_id,
            folder_id=int(folder_id) if folder_id is not None else None,
            move_to_root=move_to_root,
            source='agent',
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    # 移动是纯归属变更，不回传正文（native 文档 _document_dict 会带 content，剔除避免噪声）。
    result.pop('content', None)
    return {'moved': True, **result}


# ---------- 目录（folder）：分身帮主人维护知识库目录树（全套 CRUD）----------


async def handle_knowledge_list_folders(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_folders：列某可达知识库的目录树（平铺，按 parent_id 组树）。"""
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    folders = await knowledge_service.list_folders(db, agent.owner_hasn_id, kb_id)
    return {'folders': folders}


async def handle_knowledge_create_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.create_folder：在可达知识库里新建目录。"""
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    parent_id = input_payload.get('parent_id')
    return await knowledge_service.create_folder(
        db,
        agent.owner_hasn_id,
        kb_id,
        name=str(input_payload['name']),
        parent_id=int(parent_id) if parent_id is not None else None,
    )


async def handle_knowledge_update_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.update_folder：重命名/移动目录（按 folder_id 反查所属 kb 做可达性闸门）。"""
    folder_id = int(input_payload['folder_id'])
    folder = await knowledge_service.get_folder(db, agent.owner_hasn_id, folder_id)
    await _assert_kb_reachable(db, agent, int(folder['kb_id']))
    parent_id = input_payload.get('parent_id')
    return await knowledge_service.update_folder(
        db,
        agent.owner_hasn_id,
        folder_id,
        name=input_payload.get('name'),
        parent_id=int(parent_id) if parent_id is not None else None,
        move_to_root=bool(input_payload.get('move_to_root')),
    )


async def handle_knowledge_delete_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_folder：删空目录（非空如实拒）。"""
    folder_id = int(input_payload['folder_id'])
    folder = await knowledge_service.get_folder(db, agent.owner_hasn_id, folder_id)
    await _assert_kb_reachable(db, agent, int(folder['kb_id']))
    await knowledge_service.delete_folder(db, agent.owner_hasn_id, folder_id)
    return {'deleted': True, 'folder_id': folder_id}


async def handle_knowledge_write_doc(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.write_doc：创建/更新原生文档（D9）；返回 doc_id。"""
    doc_id = input_payload.get('doc_id')
    try:
        if doc_id is not None:
            doc = await knowledge_service.get_document(db, agent.owner_hasn_id, int(doc_id))
            await _assert_kb_reachable(db, agent, int(doc['kb_id']))
            result = await knowledge_service.update_native_document(
                db,
                agent.owner_hasn_id,
                int(doc_id),
                title=input_payload.get('title'),
                content=input_payload.get('content'),
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
        else:
            kb_id = int(input_payload['kb_id'])
            await _assert_kb_reachable(db, agent, kb_id)
            folder_id = input_payload.get('folder_id')
            result = await knowledge_service.create_native_document(
                db,
                agent.owner_hasn_id,
                kb_id,
                title=str(input_payload['title']),
                content=str(input_payload['content']),
                folder_id=int(folder_id) if folder_id is not None else None,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    result.pop('content', None)
    return {'doc_id': result['id'], **result}
