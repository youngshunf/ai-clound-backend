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
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
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
    """knowledge.upload_document：content_text → service 侧转文件（Agent 不构造 multipart）。"""
    kb_id = int(input_payload['kb_id'])
    await _assert_kb_reachable(db, agent, kb_id)
    title = str(input_payload['title']).strip() or 'untitled'
    filename = title if '.' in title.rsplit('/', 1)[-1] else f'{title}.txt'
    folder_id = input_payload.get('folder_id')
    try:
        return await knowledge_service.upload_file_document(
            db,
            agent.owner_hasn_id,
            kb_id,
            filename=filename,
            data=str(input_payload['content_text']).encode('utf-8'),
            mime='text/plain',
            folder_id=int(folder_id) if folder_id is not None else None,
            source='agent',
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc


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
