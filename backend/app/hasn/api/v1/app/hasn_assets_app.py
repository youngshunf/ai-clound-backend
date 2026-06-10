"""HASN 资产 - 用户端 API（Owner JWT，09 Stage1e）。

- POST /upload   ：owner 上传文件/图片/语音到私有桶（dm_attachment），注册 hasn_assets，返回 asset_id。
- POST /resolve  ：批量 asset_ids + 会话上下文 → 鉴权(三态) → display_url + expires_at。
  daemon `/api/v1/owner/uploads/file` 代理到 /upload；daemon 投影前对附件调 /resolve 预解析 display_url。

owner 身份由 JWT → hasn_humans.hasn_id 解析。私有附件越权由 resolve 鉴权 + 会话 grant 关闭（1f）。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, File, Form, Request, UploadFile

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.asset_api import ResolveAssetsParam, ResolvedAssetItem, UploadedAsset
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.plugin.s3.service.storage_service import storage_service

router = APIRouter()

# 上传上限（Stage 0 定）：图 10MB / 语音 25MB / 文件 50MB。
_MAX_SIZE = {'image': 10 * 1024 * 1024, 'voice': 25 * 1024 * 1024, 'file': 50 * 1024 * 1024}

# 本端点允许的 category（默认消息附件；published_artifact 为模块 18 网页发布制品，私有桶、不抽取）。
_ALLOWED_CATEGORIES = {'dm_attachment', 'published_artifact'}
# 发布制品上限（bundle-zip 可较大）。
_PUBLISHED_ARTIFACT_MAX_SIZE = 200 * 1024 * 1024


async def _current_owner_hasn_id(db: CurrentSession, user_id: int) -> str:
    owner_hasn_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner_hasn_id:
        raise errors.NotFoundError(msg='当前用户尚未绑定 HASN 身份')
    return owner_hasn_id


def _kind_of(mime: str) -> str:
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith('audio/'):
        return 'voice'
    return 'file'


@router.post('/upload', summary='上传消息附件（私有桶，注册资产）', dependencies=[DependsJwtAuth])
async def upload_asset(
    request: Request,
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='附件文件')],
    width: Annotated[int | None, Form()] = None,
    height: Annotated[int | None, Form()] = None,
    duration_ms: Annotated[int | None, Form()] = None,
    category: Annotated[str, Form()] = 'dm_attachment',
) -> ResponseSchemaModel[UploadedAsset]:
    if category not in _ALLOWED_CATEGORIES:
        raise errors.RequestError(msg=f'不支持的资产类别：{category}')
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    content_type = file.content_type or 'application/octet-stream'
    kind = _kind_of(content_type)

    data = await file.read()
    if not data:
        raise errors.RequestError(msg='文件为空')
    # 发布制品（模块 18）走独立大上限；其余按 kind 限额。
    is_published = category == 'published_artifact'
    limit = _PUBLISHED_ARTIFACT_MAX_SIZE if is_published else _MAX_SIZE.get(kind, _MAX_SIZE['file'])
    if len(data) > limit:
        raise errors.RequestError(msg=f'{kind} 超出大小上限 {limit // (1024 * 1024)}MB')

    ref = await storage_service.upload(
        db, data, category=category, filename=file.filename, content_type=content_type
    )
    asset = await hasn_asset_service.register_asset(
        db,
        owner_hasn_id=owner_hasn_id,
        ref=ref,
        kind=kind,
        width=width,
        height=height,
        duration_ms=duration_ms,
        # 发布制品不进语义抽取流水线（自包含 HTML/zip，非消息附件）。
        extract_status='done' if is_published else 'pending',
    )
    return response_base.success(
        data=UploadedAsset(
            asset_id=asset.asset_id,
            kind=kind,
            mime=ref.mime,
            size=ref.size,
            width=width,
            height=height,
            duration_ms=duration_ms,
        )
    )


@router.post('/resolve', summary='批量解析资产为可展示 URL（鉴权三态）', dependencies=[DependsJwtAuth])
async def resolve_assets(
    request: Request,
    db: CurrentSession,
    obj: ResolveAssetsParam,
) -> ResponseSchemaModel[list[ResolvedAssetItem]]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    resolved = await hasn_asset_service.resolve(
        db,
        requester_hasn_id=owner_hasn_id,
        asset_ids=obj.asset_ids,
        conversation_id=obj.conversation_id,
        expires_in=obj.expires_in,
    )
    items = [ResolvedAssetItem(asset_id=r.asset_id, display_url=r.display_url, expires_at=r.expires_at) for r in resolved]
    return response_base.success(data=items)
