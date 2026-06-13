"""知识库 Agent 端 API。

路由前缀: /api/v1/knowledge/agent
认证方式: Agent JWT（身份恒取自 JWT claims，绝不读请求体身份字段）。

每个入口先过：维度① scope 三态（云端 Gateway/本面不重复实现 ask 链，但读写分面由 scope 体现）→
维度② grant（denied 拒 / restricted 裁剪，交集空即拒）→ 行级 owner 隔离（service 内强制）。
本面与 App 工具（gateway_internal handler）共用同一组 tool_handlers——同一套权限、同一处审计语义。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.3 Agent surface。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.hasn_knowledge.service import tool_handlers
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class AgentSearchRequest(BaseModel):
    query: str = Field(min_length=1, description='检索问题')
    kb_ids: list[int] | None = Field(default=None, description='限定知识库（空=可达全部）')
    limit: int = Field(default=8, ge=1, le=50, description='返回片段数')
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)


class AgentUploadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200, description='文档标题/文件名')
    content_text: str | None = Field(default=None, min_length=1, description='文本内容（与 asset_uri 二选一）')
    asset_uri: str | None = Field(
        default=None, pattern='^hasn://asset/', description='已在私有桶的真实文件引用（与 content_text 二选一）'
    )
    folder_id: int | None = Field(default=None, description='目录 ID（空=库根）')


class AgentWriteDocRequest(BaseModel):
    kb_id: int | None = Field(default=None, description='创建时必填')
    doc_id: int | None = Field(default=None, description='更新时必填')
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None)
    folder_id: int | None = Field(default=None)


class AgentFolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, description='目录名')
    parent_id: int | None = Field(default=None, description='父目录 ID（空=库根）')


class AgentFolderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100, description='重命名')
    parent_id: int | None = Field(default=None, description='移动到该父目录')
    move_to_root: bool = Field(default=False, description='移到库根')


@router.post('/search', summary='Agent 检索知识库（hasn.knowledge.search）', name='knowledge_agent_search')
async def agent_search(
    db: CurrentSession, agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], body: AgentSearchRequest
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_search(db, agent, body.model_dump())
    return response_base.success(data=result)


@router.get('/kbs', summary='Agent 列可达知识库（hasn.knowledge.list_datasets）')
async def agent_list_kbs(
    db: CurrentSession, agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth]
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_list_datasets(db, agent, {})
    return response_base.success(data=result)


@router.get('/documents/{doc_id}', summary='Agent 读文档解析文本（hasn.knowledge.fetch_doc）')
async def agent_fetch_doc(
    db: CurrentSession, agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], doc_id: int
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_fetch_doc(db, agent, {'doc_id': doc_id})
    return response_base.success(data=result)


@router.post('/kbs/{kb_id}/documents', summary='Agent 上传文档（hasn.knowledge.upload_document）')
async def agent_upload_document(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    kb_id: int,
    body: AgentUploadRequest,
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_upload_document(db, agent, {'kb_id': kb_id, **body.model_dump()})
    return response_base.success(data=result)


@router.post('/documents/native', summary='Agent 创建原生文档（hasn.knowledge.write_doc）')
async def agent_create_native(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentWriteDocRequest,
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_write_doc(db, agent, body.model_dump(exclude={'doc_id'}))
    return response_base.success(data=result)


@router.put('/documents/{doc_id}', summary='Agent 更新原生文档（hasn.knowledge.write_doc）')
async def agent_update_native(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    doc_id: int,
    body: AgentWriteDocRequest,
) -> ResponseModel:
    payload = body.model_dump(exclude={'doc_id', 'kb_id'})
    payload['doc_id'] = doc_id
    result = await tool_handlers.handle_knowledge_write_doc(db, agent, payload)
    return response_base.success(data=result)


@router.get('/kbs/{kb_id}/folders', summary='Agent 列目录树（hasn.knowledge.list_folders）')
async def agent_list_folders(
    db: CurrentSession, agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], kb_id: int
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_list_folders(db, agent, {'kb_id': kb_id})
    return response_base.success(data=result)


@router.post('/kbs/{kb_id}/folders', summary='Agent 新建目录（hasn.knowledge.create_folder）')
async def agent_create_folder(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    kb_id: int,
    body: AgentFolderCreateRequest,
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_create_folder(db, agent, {'kb_id': kb_id, **body.model_dump()})
    return response_base.success(data=result)


@router.put('/folders/{folder_id}', summary='Agent 重命名/移动目录（hasn.knowledge.update_folder）')
async def agent_update_folder(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    folder_id: int,
    body: AgentFolderUpdateRequest,
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_update_folder(db, agent, {'folder_id': folder_id, **body.model_dump()})
    return response_base.success(data=result)


@router.delete('/folders/{folder_id}', summary='Agent 删除空目录（hasn.knowledge.delete_folder）')
async def agent_delete_folder(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    folder_id: int,
) -> ResponseModel:
    result = await tool_handlers.handle_knowledge_delete_folder(db, agent, {'folder_id': folder_id})
    return response_base.success(data=result)
