"""知识库用户端 API。

路由前缀: /api/v1/knowledge/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析），owner 隔离；daemon 代理为 /api/v1/knowledge/*。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.3 App surface。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.hasn_core import hasn_humans_dao
from backend.app.hasn_knowledge.service.error_adapter import to_http_error
from backend.app.hasn_knowledge.service.knowledge_service import Subject, knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth

# CurrentSession/CurrentSessionTransaction 是 FastAPI 依赖注入的运行期注解（Annotated[..., Depends(...)]），
# 必须运行期导入——即便有 from __future__ import annotations，FastAPI 仍用 get_type_hints 在运行期求值，
# 放进 TYPE_CHECKING 会 NameError。ruff TC001 自动建议在此**不适用**。
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id（owner 隔离键）。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


class CreateKbRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128, description='库名')
    description: str | None = Field(default=None, max_length=512, description='描述')
    cover_asset_uri: str | None = Field(
        default=None, max_length=512, description='封面资产 hasn://asset/（主人上传得到，可选）'
    )
    # doc38 §5.5 容器创建时的项目归属：daemon 代主人建库（派分身建库）时带上本次派发定稿的项目，
    # 新库直接进项目「挂靠资源区」；缺省不挂。非本主人的项目 → service 侧 404。
    platform_project_id: str | None = Field(default=None, description='挂进的平台项目 id（云端权威 UUID，可选）')


class UpdateKbRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128, description='库名（空=不改）')
    description: str | None = Field(default=None, max_length=512, description='描述（空串=清空）')
    cover_asset_uri: str | None = Field(
        default=None, max_length=512, description='封面资产 hasn://asset/（空串=清空封面；不传=不改）'
    )


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128, description='目录名')
    parent_id: int | None = Field(default=None, description='父目录 ID（空=库根）')


class UpdateFolderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128, description='重命名')
    parent_id: int | None = Field(default=None, description='移动到目录 ID')
    move_to_root: bool = Field(default=False, description='移动到库根')


class CreateNativeDocRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200, description='标题')
    content: str = Field(default='', description='Markdown 正文')
    folder_id: int | None = Field(default=None, description='目录 ID（空=库根）')


class UpdateDocumentRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200, description='标题')
    content: str | None = Field(default=None, description='Markdown 正文（仅 native）')
    folder_id: int | None = Field(default=None, description='移动到目录 ID')
    move_to_root: bool = Field(default=False, description='移动到库根')


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, description='检索问题')
    kb_ids: list[int] | None = Field(default=None, description='限定知识库（空=全部）')
    top_k: int = Field(default=8, ge=1, le=50, description='返回片段数')
    similarity_threshold: float | None = Field(default=None, ge=0, le=1, description='相似度阈值')
    agent_hasn_id: str | None = Field(default=None, description='以分身视角检索（grant 预览）')


class PutGrantRequest(BaseModel):
    mode: str = Field(description='授权模式 inherit/restricted/denied')
    kb_ids: list[int] = Field(default_factory=list, description='restricted 白名单')


class SetVisibilityRequest(BaseModel):
    visibility: str = Field(description='private/enterprise/link')
    enterprise_id: int | None = Field(default=None, description='设企业可见时归属的企业 ID')


class AddShareRequest(BaseModel):
    grantee_type: str = Field(description='human/agent/enterprise')
    grantee_id: str = Field(description='被授权对象 ID')
    permission: str = Field(description='viewer/editor/manager')


class AddDocShareRequest(BaseModel):
    grantee_type: str = Field(description='human/agent（文档级无企业/可见性档）')
    grantee_id: str = Field(description='被授权对象 ID')
    permission: str = Field(description='viewer/editor/manager')


# ---------- kb ----------


@router.get('/kbs', summary='列知识库（我的 ∪ 共享给我的 ∪ 企业可见）', dependencies=[DependsJwtAuth])
async def list_kbs(
    request: Request,
    db: CurrentSession,
    platform_project_id: Annotated[str | None, Query(description='按挂靠的平台项目收窄（可选；缺省列全部）')] = None,
) -> ResponseModel:
    # 缺省不按项目过滤（doc38 §5.6 读侧不收窄）；项目总览「挂靠资源区」显式传参逐应用查。
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.list_accessible_kbs(
        db, subject=Subject.human(owner_id), platform_project_id=platform_project_id
    )
    return response_base.success(data=data)


@router.post('/kbs', summary='建知识库（同步建处理后端索引库）', dependencies=[DependsJwtAuth])
async def create_kb(request: Request, db: CurrentSessionTransaction, body: CreateKbRequest) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    try:
        data = await knowledge_service.create_kb(
            db,
            owner_id,
            name=body.name,
            description=body.description,
            cover_asset_uri=body.cover_asset_uri,
            platform_project_id=body.platform_project_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await knowledge_service.write_ui_audit(
        db, owner_id=owner_id, user_id=request.user.id, method='ui.create_kb', context={'kb_id': data['id']}
    )
    return response_base.success(data=data)


@router.get('/kbs/{kb_id}', summary='知识库详情（含挂靠的平台项目）', dependencies=[DependsJwtAuth])
async def get_kb(request: Request, db: CurrentSession, kb_id: int) -> ResponseModel:
    # 单库详情：daemon 派发时据此继承容器挂靠的项目（doc38 §5.2），webui 详情页也免于从整张 list 里筛。
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.get_kb_detail(db, subject=Subject.human(owner_id), kb_id=kb_id)
    return response_base.success(data=data)


@router.put('/kbs/{kb_id}', summary='改知识库名称 / 描述（需 manager 权）', dependencies=[DependsJwtAuth])
async def update_kb(
    request: Request, db: CurrentSessionTransaction, kb_id: int, body: UpdateKbRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='manager')
    data = await knowledge_service.update_kb(
        db, kb.owner_id, kb_id, name=body.name, description=body.description, cover_asset_uri=body.cover_asset_uri
    )
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.update_kb',
        context={'kb_id': kb_id, 'actor': owner_id},
    )
    return response_base.success(data=data)


@router.delete('/kbs/{kb_id}', summary='删知识库（级联删索引库与文档行，需 manager 权）', dependencies=[DependsJwtAuth])
async def delete_kb(request: Request, db: CurrentSessionTransaction, kb_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='manager')
    try:
        await knowledge_service.delete_kb(db, kb.owner_id, kb_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.delete_kb',
        context={'kb_id': kb_id, 'actor': owner_id},
    )
    return response_base.success()


# ---------- 共享管理（仅 manager 权） ----------


@router.get(
    '/kbs/{kb_id}/shares',
    summary='查看知识库共享名单',
    name='knowledge_app_list_shares',
    dependencies=[DependsJwtAuth],
)
async def list_shares(request: Request, db: CurrentSession, kb_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.list_shares(db, subject=Subject.human(owner_id), kb_id=kb_id)
    return response_base.success(data=data)


@router.put(
    '/kbs/{kb_id}/visibility',
    summary='设置可见性（私有/企业可见/链接）',
    name='knowledge_app_set_visibility',
    dependencies=[DependsJwtAuth],
)
async def set_visibility(
    request: Request, db: CurrentSessionTransaction, kb_id: int, body: SetVisibilityRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.set_visibility(
        db, subject=Subject.human(owner_id), kb_id=kb_id, visibility=body.visibility, enterprise_id=body.enterprise_id
    )
    return response_base.success(data=data)


@router.post(
    '/kbs/{kb_id}/shares',
    summary='添加/更新协作者（人/分身/企业）',
    name='knowledge_app_add_share',
    dependencies=[DependsJwtAuth],
)
async def add_share(
    request: Request, db: CurrentSessionTransaction, kb_id: int, body: AddShareRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.add_share(
        db,
        subject=Subject.human(owner_id),
        kb_id=kb_id,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.delete(
    '/kbs/{kb_id}/shares',
    summary='撤销协作者',
    name='knowledge_app_revoke_share',
    dependencies=[DependsJwtAuth],
)
async def revoke_share(
    request: Request, db: CurrentSessionTransaction, kb_id: int, grantee_type: str, grantee_id: str
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    ok = await knowledge_service.revoke_share(
        db, subject=Subject.human(owner_id), kb_id=kb_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return response_base.success(data={'revoked': ok})


# ---------- 单个文档级共享（仅 manager 权；文档协作者仅 human/agent）----------


@router.get(
    '/documents/{doc_id}/shares',
    summary='查看文档共享名单',
    name='knowledge_app_list_doc_shares',
    dependencies=[DependsJwtAuth],
)
async def list_doc_shares(request: Request, db: CurrentSession, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.list_doc_shares(db, subject=Subject.human(owner_id), doc_id=doc_id)
    return response_base.success(data=data)


@router.post(
    '/documents/{doc_id}/shares',
    summary='添加/更新文档协作者（人/分身）',
    name='knowledge_app_add_doc_share',
    dependencies=[DependsJwtAuth],
)
async def add_doc_share(
    request: Request, db: CurrentSessionTransaction, doc_id: int, body: AddDocShareRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.add_doc_share(
        db,
        subject=Subject.human(owner_id),
        doc_id=doc_id,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.delete(
    '/documents/{doc_id}/shares',
    summary='撤销文档协作者',
    name='knowledge_app_revoke_doc_share',
    dependencies=[DependsJwtAuth],
)
async def revoke_doc_share(
    request: Request, db: CurrentSessionTransaction, doc_id: int, grantee_type: str, grantee_id: str
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    ok = await knowledge_service.revoke_doc_share(
        db, subject=Subject.human(owner_id), doc_id=doc_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return response_base.success(data={'revoked': ok})


# ---------- folders（D9）----------


@router.get('/kbs/{kb_id}/folders', summary='目录树（平铺，前端按 parent_id 组树）', dependencies=[DependsJwtAuth])
async def list_folders(request: Request, db: CurrentSession, kb_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='viewer')
    return response_base.success(data=await knowledge_service.list_folders(db, kb.owner_id, kb_id))


@router.post('/kbs/{kb_id}/folders', summary='建目录', dependencies=[DependsJwtAuth])
async def create_folder(
    request: Request, db: CurrentSessionTransaction, kb_id: int, body: CreateFolderRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='editor')
    data = await knowledge_service.create_folder(db, kb.owner_id, kb_id, name=body.name, parent_id=body.parent_id)
    return response_base.success(data=data)


@router.put('/folders/{folder_id}', summary='重命名/移动目录', dependencies=[DependsJwtAuth])
async def update_folder(
    request: Request, db: CurrentSessionTransaction, folder_id: int, body: UpdateFolderRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_folder(db, subject=Subject.human(owner_id), folder_id=folder_id, need='editor')
    data = await knowledge_service.update_folder(
        db, kb.owner_id, folder_id, name=body.name, parent_id=body.parent_id, move_to_root=body.move_to_root
    )
    return response_base.success(data=data)


@router.delete('/folders/{folder_id}', summary='删目录（仅空目录，非空如实拒）', dependencies=[DependsJwtAuth])
async def delete_folder(request: Request, db: CurrentSessionTransaction, folder_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_folder(db, subject=Subject.human(owner_id), folder_id=folder_id, need='editor')
    await knowledge_service.delete_folder(db, kb.owner_id, folder_id)
    return response_base.success()


# ---------- documents ----------


@router.get('/kbs/{kb_id}/documents', summary='列文档（触发索引状态读时对账）', dependencies=[DependsJwtAuth])
async def list_documents(
    request: Request,
    db: CurrentSessionTransaction,
    kb_id: int,
    folder_id: Annotated[int | None, Query(description='目录过滤：缺省=全库，0=库根，>0=指定目录')] = None,
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='viewer')
    data = await knowledge_service.list_documents(db, kb.owner_id, kb_id, folder_id=folder_id)
    return response_base.success(data=data)


@router.post('/kbs/{kb_id}/documents', summary='上传文档（原件落私有桶+推引擎副本，自动索引）', dependencies=[DependsJwtAuth])
async def upload_document(
    request: Request,
    db: CurrentSessionTransaction,
    kb_id: int,
    file: Annotated[UploadFile, File(description='文档文件')],
    folder_id: Annotated[int | None, Query(description='目录 ID（空=库根）')] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias='Idempotency-Key', min_length=1, max_length=96, description='上传幂等键'),
    ] = None,
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='editor')
    data_bytes = await file.read()
    try:
        data = await knowledge_service.upload_file_document(
            db,
            kb.owner_id,
            kb_id,
            filename=file.filename or 'untitled',
            data=data_bytes,
            mime=file.content_type or 'application/octet-stream',
            folder_id=folder_id,
            source='ui',
            idempotency_key=idempotency_key,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await knowledge_service.write_ui_audit(
        db,
        owner_id=kb.owner_id,
        user_id=request.user.id,
        method='ui.upload_document',
        context={'kb_id': kb_id, 'doc_id': data['id'], 'name': data['name'], 'actor': owner_id},
    )
    return response_base.success(data=data)


@router.get('/documents/{doc_id}', summary='文档详情（native 含正文）', dependencies=[DependsJwtAuth])
async def get_document(request: Request, db: CurrentSession, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='viewer')
    return response_base.success(data=await knowledge_service.get_document(db, kb.owner_id, doc_id))


@router.delete('/documents/{doc_id}', summary='删文档（引擎副本删净）', dependencies=[DependsJwtAuth])
async def delete_document(request: Request, db: CurrentSessionTransaction, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='editor')
    try:
        await knowledge_service.delete_document(db, kb.owner_id, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.delete_document',
        context={'doc_id': doc_id, 'actor': owner_id},
    )
    return response_base.success()


@router.get(
    '/documents/{doc_id}/download',
    summary='下载原文件（私有桶流式，不经处理引擎，D10）',
    dependencies=[DependsJwtAuth],
)
async def download_document(request: Request, db: CurrentSession, doc_id: int) -> StreamingResponse:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='viewer')
    name, mime, stream = await knowledge_service.download_document(db, kb.owner_id, doc_id)
    return StreamingResponse(
        stream,
        media_type=mime,
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.post('/documents/{doc_id}/reindex', summary='重新索引（file 从桶重放/native 以正文重放）', dependencies=[DependsJwtAuth])
async def reindex_document(request: Request, db: CurrentSessionTransaction, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='editor')
    try:
        data = await knowledge_service.reindex_document(db, kb.owner_id, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.reindex_document',
        context={'doc_id': doc_id, 'actor': owner_id},
    )
    return response_base.success(data=data)


# ---------- native documents（D9）----------


@router.post('/kbs/{kb_id}/documents/native', summary='创建原生文档（正文落 PG，保存即索引）', dependencies=[DependsJwtAuth])
async def create_native_document(
    request: Request, db: CurrentSessionTransaction, kb_id: int, body: CreateNativeDocRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_kb(db, subject=Subject.human(owner_id), kb_id=kb_id, need='editor')
    data = await knowledge_service.create_native_document(
        db, kb.owner_id, kb_id, title=body.title, content=body.content, folder_id=body.folder_id, source='ui'
    )
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.create_native_doc',
        context={'kb_id': kb_id, 'doc_id': data['id'], 'actor': owner_id},
    )
    return response_base.success(data=data)


@router.put('/documents/{doc_id}', summary='更新文档（native 保存即重索引；移动目录不重索引）', dependencies=[DependsJwtAuth])
async def update_document(
    request: Request, db: CurrentSessionTransaction, doc_id: int, body: UpdateDocumentRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='editor')
    data = await knowledge_service.update_native_document(
        db,
        kb.owner_id,
        doc_id,
        title=body.title,
        content=body.content,
        folder_id=body.folder_id,
        move_to_root=body.move_to_root,
        source='ui',
    )
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.update_document',
        context={'doc_id': doc_id, 'actor': owner_id},
    )
    return response_base.success(data=data)


@router.get('/documents/{doc_id}/content', summary='原生文档正文（PG 权威，不经引擎）', dependencies=[DependsJwtAuth])
async def get_native_content(request: Request, db: CurrentSession, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='viewer')
    return response_base.success(data=await knowledge_service.get_native_content(db, kb.owner_id, doc_id))


@router.get('/documents/{doc_id}/versions', summary='版本历史', dependencies=[DependsJwtAuth])
async def list_versions(request: Request, db: CurrentSession, doc_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='viewer')
    return response_base.success(data=await knowledge_service.list_versions(db, kb.owner_id, doc_id))


@router.get('/documents/{doc_id}/versions/{version_no}', summary='版本快照', dependencies=[DependsJwtAuth])
async def get_version(request: Request, db: CurrentSession, doc_id: int, version_no: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='viewer')
    return response_base.success(data=await knowledge_service.get_version(db, kb.owner_id, doc_id, version_no))


@router.post(
    '/documents/{doc_id}/versions/{version_no}/restore',
    summary='恢复版本（落新版本+重索引）',
    dependencies=[DependsJwtAuth],
)
async def restore_version(
    request: Request, db: CurrentSessionTransaction, doc_id: int, version_no: int
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    kb = await knowledge_service.authorize_doc(db, subject=Subject.human(owner_id), doc_id=doc_id, need='editor')
    data = await knowledge_service.restore_version(db, kb.owner_id, doc_id, version_no, source='ui')
    await knowledge_service.write_ui_audit(
        db, owner_id=kb.owner_id, user_id=request.user.id, method='ui.restore_version',
        context={'doc_id': doc_id, 'version_no': version_no, 'actor': owner_id},
    )
    return response_base.success(data=data)


# ---------- 检索 / 审计 / 授权 ----------


@router.post('/search', summary='检索（dataset 注入由 service 单点收口）', dependencies=[DependsJwtAuth])
async def search(request: Request, db: CurrentSessionTransaction, body: SearchRequest) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    try:
        data = await knowledge_service.search(
            db,
            owner_id,
            question=body.question,
            kb_ids=body.kb_ids,
            top_k=body.top_k,
            similarity_threshold=body.similarity_threshold,
            agent_hasn_id=body.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    # 检索审计（设计 §3.3）：记 result_count + kb 覆盖度，支撑「异常批量检索提醒」。
    await knowledge_service.write_ui_audit(
        db,
        owner_id=owner_id,
        user_id=request.user.id,
        method='ui.search',
        context={
            'question': body.question[:120],
            'kb_ids': body.kb_ids,
            'agent_view': body.agent_hasn_id,
            'result_count': data.get('total'),
            'kb_count': data.get('kb_count'),
        },
    )
    return response_base.success(data=data)


@router.get('/audit', summary='知识库操作审计（域审计+App 工具审计合并视图）', dependencies=[DependsJwtAuth])
async def list_audit(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    return response_base.success(data=await knowledge_service.list_audit(db, owner_id, limit=limit, offset=offset))


@router.get('/agent-grants/{agent_hasn_id}', summary='读分身知识库白名单（维度②）', dependencies=[DependsJwtAuth])
async def get_agent_grant(request: Request, db: CurrentSession, agent_hasn_id: str) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    return response_base.success(data=await knowledge_service.get_agent_grant(db, owner_id, agent_hasn_id))


@router.put('/agent-grants/{agent_hasn_id}', summary='写分身知识库白名单（维度②）', dependencies=[DependsJwtAuth])
async def put_agent_grant(
    request: Request, db: CurrentSessionTransaction, agent_hasn_id: str, body: PutGrantRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await knowledge_service.put_agent_grant(db, owner_id, agent_hasn_id, mode=body.mode, kb_ids=body.kb_ids)
    await knowledge_service.write_ui_audit(
        db, owner_id=owner_id, user_id=request.user.id, method='ui.update_agent_grant',
        context={'agent_hasn_id': agent_hasn_id, 'mode': body.mode},
    )
    return response_base.success(data=data)
