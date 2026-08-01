"""用户私有存储 Owner API。

身份只从 Owner JWT 映射的 HASN human 取得，所有查询与写入均由服务层追加
``owner_hasn_id`` 行级条件；不存在与越权使用相同 404 契约。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.asset_api import StartMultipartParam, UploadedAsset
from backend.app.hasn.schema.owner_storage_api import (
    CreateStorageExportParam,
    CreateStorageFolderParam,
    SaveStorageAssetParam,
    UpdateStorageEntryParam,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, async_db_session

router = APIRouter()
_owner_storage = OwnerStorageService(async_db_session)
_MULTIPART_CATEGORIES = {
    'dm_attachment',
    'private_doc',
    'published_artifact',
    'user_upload',
    'user_avatar',
    'post_image',
}


async def _current_owner_hasn_id(db: CurrentSession, user_id: int) -> str:
    owner_hasn_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner_hasn_id:
        raise errors.NotFoundError(msg='当前用户尚未绑定 HASN 身份')
    return str(owner_hasn_id)


@router.post('/multipart', summary='初始化受控分片上传', dependencies=[DependsJwtAuth])
async def start_owner_storage_multipart_upload(
    request: Request,
    db: CurrentSession,
    obj: StartMultipartParam,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
) -> ResponseSchemaModel[dict]:
    if obj.category not in _MULTIPART_CATEGORIES:
        raise errors.RequestError(
            code=422,
            msg='STORAGE_CATEGORY_UNSUPPORTED',
            data={'category': obj.category},
        )
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.start_multipart(
            owner_hasn_id=owner,
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
async def upload_owner_storage_multipart_part(
    request: Request,
    db: CurrentSession,
    upload_id: str,
    part_number: int,
    file: Annotated[UploadFile, File(description='当前分片')],
    size: Annotated[int, Form(gt=0)],
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.upload_multipart_part(
            owner_hasn_id=owner,
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
async def complete_owner_storage_multipart_upload(
    request: Request,
    db: CurrentSession,
    upload_id: str,
) -> ResponseSchemaModel[UploadedAsset]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    stored = await _owner_storage.complete_multipart(
        owner_hasn_id=owner,
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
async def abort_owner_storage_multipart_upload(
    request: Request,
    db: CurrentSession,
    upload_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.abort_multipart(
            owner_hasn_id=owner,
            upload_id=upload_id,
        )
    )


@router.get('/usage', summary='读取当前 Owner 存储用量', dependencies=[DependsJwtAuth])
async def get_storage_usage(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.usage_details(owner_hasn_id=owner))


@router.get('/entries', summary='分页读取文件与目录', dependencies=[DependsJwtAuth])
async def list_storage_entries(
    request: Request,
    db: CurrentSession,
    parent_entry_id: Annotated[str | None, Query(max_length=40)] = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
    entry_type: Annotated[str | None, Query(pattern='^(file|folder)$')] = None,
    category: Annotated[str | None, Query(max_length=32)] = None,
    source_app: Annotated[str | None, Query(max_length=64)] = None,
    lifecycle_status: Annotated[
        str,
        Query(pattern='^(active|trashed|deleting|deleted)$'),
    ] = 'active',
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    data = await _owner_storage.list_entries(
        owner_hasn_id=owner,
        parent_entry_id=parent_entry_id,
        query=query,
        entry_type=entry_type,
        category=category,
        source_app=source_app,
        lifecycle_status=lifecycle_status,
        page=page,
        page_size=page_size,
    )
    return response_base.success(data=data)


@router.get('/entries/{entry_id}', summary='读取文件或目录详情', dependencies=[DependsJwtAuth])
async def get_storage_entry(
    request: Request,
    db: CurrentSession,
    entry_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.entry_details(owner_hasn_id=owner, entry_id=entry_id))


@router.post('/folders', summary='新建逻辑文件夹', dependencies=[DependsJwtAuth])
async def create_storage_folder(
    request: Request,
    db: CurrentSession,
    obj: CreateStorageFolderParam,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    data = await _owner_storage.create_folder(
        owner_hasn_id=owner,
        name=obj.name,
        parent_entry_id=obj.parent_entry_id,
    )
    return response_base.success(data=data)


@router.patch('/entries/{entry_id}', summary='重命名或移动目录项', dependencies=[DependsJwtAuth])
async def update_storage_entry(
    request: Request,
    db: CurrentSession,
    entry_id: str,
    obj: UpdateStorageEntryParam,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    current = await _owner_storage.entry_details(owner_hasn_id=owner, entry_id=entry_id)
    parent = obj.parent_entry_id if 'parent_entry_id' in obj.model_fields_set else current['parent_entry_id']
    data = await _owner_storage.update_entry(
        owner_hasn_id=owner,
        entry_id=entry_id,
        version=obj.version,
        name=obj.name,
        parent_entry_id=parent,
    )
    return response_base.success(data=data)


@router.post('/assets/{asset_id}/trash', summary='把资产移入垃圾箱', dependencies=[DependsJwtAuth])
async def trash_storage_asset(
    request: Request,
    db: CurrentSession,
    asset_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.trash_asset(owner_hasn_id=owner, asset_id=asset_id))


@router.post('/assets/{asset_id}/restore', summary='恢复垃圾箱资产', dependencies=[DependsJwtAuth])
async def restore_storage_asset(
    request: Request,
    db: CurrentSession,
    asset_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.restore_asset(owner_hasn_id=owner, asset_id=asset_id))


@router.get('/assets/{asset_id}/references', summary='读取资产活动引用', dependencies=[DependsJwtAuth])
async def list_storage_asset_references(
    request: Request,
    db: CurrentSession,
    asset_id: str,
) -> ResponseSchemaModel[list[dict]]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(
        data=await _owner_storage.asset_references(owner_hasn_id=owner, asset_id=asset_id)
    )


@router.delete('/assets/{asset_id}', summary='请求彻底删除资产', dependencies=[DependsJwtAuth])
async def delete_storage_asset(
    request: Request,
    db: CurrentSession,
    asset_id: str,
    cascade: Annotated[bool, Query()] = False,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    data = await _owner_storage.delete_asset(
        owner_hasn_id=owner,
        asset_id=asset_id,
        cascade=cascade,
    )
    return response_base.success(data=data)


@router.post(
    '/assets/{asset_id}/save-to-my-storage',
    summary='保存可读资产为当前 Owner 独立副本',
    dependencies=[DependsJwtAuth],
)
async def save_storage_asset(
    request: Request,
    db: CurrentSession,
    asset_id: str,
    obj: SaveStorageAssetParam,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    data = await _owner_storage.save_to_my_storage(
        owner_hasn_id=owner,
        source_asset_id=asset_id,
        idempotency_key=obj.idempotency_key,
        parent_entry_id=obj.parent_entry_id,
        display_name=obj.display_name,
    )
    return response_base.success(data=data)


@router.post('/exports', summary='创建存储导出作业', dependencies=[DependsJwtAuth])
async def create_storage_export(
    request: Request,
    db: CurrentSession,
    obj: CreateStorageExportParam,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    data = await _owner_storage.create_export(
        owner_hasn_id=owner,
        mode=obj.mode,
        include_trashed=obj.include_trashed,
    )
    return response_base.success(data=data)


@router.get('/exports', summary='列出本人的存储导出作业', dependencies=[DependsJwtAuth])
async def list_storage_exports(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=50, description='返回条数')] = 5,
) -> ResponseSchemaModel[dict]:
    """按创建时间倒序列出导出作业，供客户端重开页面后恢复「进行中 / 可下载」状态。"""
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.list_exports(owner_hasn_id=owner, limit=limit))


@router.get('/exports/{job_id}', summary='读取存储导出进度', dependencies=[DependsJwtAuth])
async def get_storage_export(
    request: Request,
    db: CurrentSession,
    job_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.export_status(owner_hasn_id=owner, job_id=job_id))


@router.post('/exports/{job_id}/download', summary='生成导出产物短期下载地址', dependencies=[DependsJwtAuth])
async def download_storage_export(
    request: Request,
    db: CurrentSession,
    job_id: str,
) -> ResponseSchemaModel[dict]:
    owner = await _current_owner_hasn_id(db, request.user.id)
    return response_base.success(data=await _owner_storage.export_download(owner_hasn_id=owner, job_id=job_id))
