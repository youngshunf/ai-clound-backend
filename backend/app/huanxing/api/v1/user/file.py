"""旧 OwnerKey 上传入口的用户云存储薄代理。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Header, UploadFile
from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.owner_key_auth import DependsOwnerKeyAuth
from backend.database.db import CurrentSession, async_db_session

router = APIRouter()
_owner_storage = OwnerStorageService(async_db_session)
_UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.post('/upload', summary='用户 Agent 文件上传（兼容入口）')
async def user_upload_s3_files(
    db: CurrentSession,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    user_id: int = DependsOwnerKeyAuth,
) -> ResponseSchemaModel[UploadUrl]:
    owner_hasn_id = (
        await db.execute(
            text("SELECT hasn_id FROM hasn_humans WHERE user_id = :user_id AND status = 'active' LIMIT 1"),
            {'user_id': user_id},
        )
    ).scalar_one_or_none()
    if owner_hasn_id is None:
        raise errors.NotFoundError(msg='STORAGE_OWNER_IDENTITY_NOT_READY')

    async def chunks():
        while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
            yield chunk

    stored = await _owner_storage.upload(
        owner_hasn_id=str(owner_hasn_id),
        chunks=chunks(),
        declared_size=file.size,
        filename=file.filename or '未命名文件',
        mime=file.content_type or 'application/octet-stream',
        category='user_upload',
        source_app='legacy_owner_key_upload',
        idempotency_key=idempotency_key,
    )
    return response_base.success(data={'url': stored.uri})
