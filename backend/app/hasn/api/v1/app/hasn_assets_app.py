"""HASN 资产 - 用户端 API（Owner JWT，09 Stage1e）。

- POST /upload   ：owner 上传文件/图片/语音到私有桶（dm_attachment），注册 hasn_assets，返回 asset_id。
- POST /resolve  ：批量 asset_ids + 会话上下文 → 鉴权(三态) → display_url + expires_at。
  daemon `/api/v1/owner/uploads/file` 代理到 /upload；daemon 投影前对附件调 /resolve 预解析 display_url。

owner 身份由 JWT → hasn_humans.hasn_id 解析。私有附件越权由 resolve 鉴权 + 会话 grant 关闭（1f）。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, File, Form, Header, Request, UploadFile

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.asset_api import (
    ResolveAssetsParam,
    ResolvedAssetItem,
    StartMultipartParam,
    UploadedAsset,
)
from backend.app.hasn.service.authz import Subject, asset_projection
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, async_db_session

router = APIRouter()

# 本端点允许的 category（默认消息附件；published_artifact 为模块 18 网页发布制品，私有桶、不抽取）。
_ALLOWED_CATEGORIES = {'dm_attachment', 'private_doc', 'published_artifact', 'user_upload', 'user_avatar', 'post_image'}
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_owner_storage = OwnerStorageService(async_db_session)


async def _current_owner_hasn_id(db: CurrentSession, user_id: int) -> str:
    owner_hasn_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner_hasn_id:
        raise errors.NotFoundError(msg='当前用户尚未绑定 HASN 身份')
    return owner_hasn_id


@router.post('/upload', summary='上传消息附件（私有桶，注册资产）', dependencies=[DependsJwtAuth])
async def upload_owner_asset(
    request: Request,
    db: CurrentSession,
    file: Annotated[UploadFile, File(description='附件文件')],
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    width: Annotated[int | None, Form()] = None,
    height: Annotated[int | None, Form()] = None,
    duration_ms: Annotated[int | None, Form()] = None,
    category: Annotated[str, Form()] = 'dm_attachment',
) -> ResponseSchemaModel[UploadedAsset]:
    if category not in _ALLOWED_CATEGORIES:
        raise errors.RequestError(code=422, msg='STORAGE_CATEGORY_UNSUPPORTED', data={'category': category})
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    content_type = file.content_type or 'application/octet-stream'
    filename = file.filename or '未命名文件'

    async def chunks():
        while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
            yield chunk

    stored = await _owner_storage.upload(
        owner_hasn_id=owner_hasn_id,
        chunks=chunks(),
        declared_size=file.size,
        filename=filename,
        mime=content_type,
        category=category,
        source_app='hasn_assets_app',
        idempotency_key=idempotency_key,
        width=width,
        height=height,
        duration_ms=duration_ms,
        extract_status='done' if category == 'published_artifact' else None,
    )
    return response_base.success(
        data=UploadedAsset(
            asset_id=stored.asset_id,
            kind=stored.kind,
            mime=stored.mime,
            size=stored.size_bytes,
            width=width,
            height=height,
            duration_ms=duration_ms,
        )
    )


@router.post('/multipart', summary='初始化受控分片上传', dependencies=[DependsJwtAuth])
async def start_owner_asset_multipart_upload(
    request: Request,
    db: CurrentSession,
    obj: StartMultipartParam,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
) -> ResponseSchemaModel[dict]:
    if obj.category not in _ALLOWED_CATEGORIES:
        raise errors.RequestError(
            code=422,
            msg='STORAGE_CATEGORY_UNSUPPORTED',
            data={'category': obj.category},
        )
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.start_multipart(
            owner_hasn_id=owner_hasn_id,
            declared_size=obj.declared_size,
            filename=obj.filename,
            mime=obj.mime,
            category=obj.category,
            source_app=obj.source_app,
            idempotency_key=idempotency_key,
            parent_entry_id=obj.parent_entry_id,
        )
    )


@router.put(
    '/multipart/{upload_id}/parts/{part_number}',
    summary='上传受控 multipart 分片',
    dependencies=[DependsJwtAuth],
)
async def upload_owner_asset_multipart_part(
    request: Request,
    db: CurrentSession,
    upload_id: str,
    part_number: int,
    file: Annotated[UploadFile, File(description='当前分片')],
    size: Annotated[int, Form(gt=0)],
) -> ResponseSchemaModel[dict]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.upload_multipart_part(
            owner_hasn_id=owner_hasn_id,
            upload_id=upload_id,
            part_number=part_number,
            file=file.file,
            size=size,
        )
    )


@router.post(
    '/multipart/{upload_id}/complete',
    summary='完成受控 multipart 上传',
    dependencies=[DependsJwtAuth],
)
async def complete_owner_asset_multipart_upload(
    request: Request,
    db: CurrentSession,
    upload_id: str,
) -> ResponseSchemaModel[UploadedAsset]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    stored = await _owner_storage.complete_multipart(
        owner_hasn_id=owner_hasn_id,
        upload_id=upload_id,
    )
    return response_base.success(
        data=UploadedAsset(
            asset_id=stored.asset_id,
            kind=stored.kind,
            mime=stored.mime,
            size=stored.size_bytes,
        )
    )


@router.delete(
    '/multipart/{upload_id}',
    summary='终止受控 multipart 上传',
    dependencies=[DependsJwtAuth],
)
async def abort_owner_asset_multipart_upload(
    request: Request,
    db: CurrentSession,
    upload_id: str,
) -> ResponseSchemaModel[dict]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.abort_multipart(
            owner_hasn_id=owner_hasn_id,
            upload_id=upload_id,
        )
    )


@router.post('/resolve', summary='批量解析资产为可展示 URL（鉴权三态）', dependencies=[DependsJwtAuth])
async def resolve_assets(
    request: Request,
    db: CurrentSession,
    obj: ResolveAssetsParam,
) -> ResponseSchemaModel[list[ResolvedAssetItem]]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    # 资产投影门（doc32 §14）：据 resource_ref 的资源 ACL（≥viewer）从请求集里挑出「该资源确实引用、
    # 且 owner 有权签发」的内嵌私有资产——泛化去 deck 硬编码，registry 分发，交集防越权。
    extra_readable = await asset_projection.readable_asset_ids(
        db,
        Subject.human(owner_hasn_id),
        obj.resource_ref,
        requested_ids=set(obj.asset_ids),
    )
    resolved = await hasn_asset_service.resolve(
        db,
        requester_hasn_id=owner_hasn_id,
        asset_ids=obj.asset_ids,
        conversation_id=obj.conversation_id,
        expires_in=obj.expires_in,
        extra_readable_asset_ids=extra_readable,
    )
    items = [
        ResolvedAssetItem(asset_id=r.asset_id, display_url=r.display_url, expires_at=r.expires_at) for r in resolved
    ]
    return response_base.success(data=items)
