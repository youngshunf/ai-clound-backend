"""旧 Agent 上传入口的用户云存储薄代理。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Header, UploadFile

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.dataclasses import AgentTokenPayload, UploadUrl
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import async_db_session

router = APIRouter()
_owner_storage = OwnerStorageService(async_db_session)
_UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.post('/upload', summary='Agent 文件上传（兼容入口）')
async def agent_upload_s3_files(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
) -> ResponseSchemaModel[UploadUrl]:
    async def chunks():
        while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
            yield chunk

    stored = await _owner_storage.upload(
        owner_hasn_id=agent.owner_hasn_id,
        chunks=chunks(),
        declared_size=file.size,
        filename=file.filename or '未命名文件',
        mime=file.content_type or 'application/octet-stream',
        category='user_upload',
        source_app='legacy_agent_upload',
        idempotency_key=idempotency_key,
    )
    return response_base.success(data={'url': stored.uri})
