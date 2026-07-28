"""HASN 资产 - Agent API。

Owner 身份只取 Agent JWT 的可信 claims，端点不接受 body/query 指定 Owner。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile

from backend.app.hasn.schema.asset_api import (
    StartMultipartParam,
    UploadedAsset,
    UploadedSourceSnapshot,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import async_db_session

router = APIRouter()
_owner_storage = OwnerStorageService(async_db_session)
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_ALLOWED_CATEGORIES = {'dm_attachment', 'private_doc', 'published_artifact', 'user_upload'}


@router.post('/upload', summary='Agent 为主人上传并登记资产')
async def upload_agent_asset(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    file: Annotated[UploadFile, File(description='附件文件')],
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    width: Annotated[int | None, Form()] = None,
    height: Annotated[int | None, Form()] = None,
    duration_ms: Annotated[int | None, Form()] = None,
    category: Annotated[str, Form()] = 'published_artifact',
    source_app: Annotated[str, Form(max_length=64)] = 'agent',
) -> ResponseSchemaModel[UploadedSourceSnapshot]:
    if category not in _ALLOWED_CATEGORIES:
        raise errors.RequestError(code=422, msg='STORAGE_CATEGORY_UNSUPPORTED', data={'category': category})
    filename = file.filename or '未命名文件'
    content_type = file.content_type or 'application/octet-stream'

    async def chunks():
        while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
            yield chunk

    stored = await _owner_storage.upload(
        owner_hasn_id=agent.owner_hasn_id,
        chunks=chunks(),
        declared_size=file.size,
        filename=filename,
        mime=content_type,
        category=category,
        source_app=source_app,
        idempotency_key=idempotency_key,
        width=width,
        height=height,
        duration_ms=duration_ms,
        extract_status='done' if category == 'published_artifact' else None,
    )
    content_sha256 = await _owner_storage.asset_content_sha256(
        owner_hasn_id=agent.owner_hasn_id,
        asset_id=stored.asset_id,
    )
    return response_base.success(
        data=UploadedSourceSnapshot(
            asset_id=stored.asset_id,
            asset_uri=stored.uri,
            kind=stored.kind,
            mime=stored.mime,
            size=stored.size_bytes,
            content_sha256=content_sha256,
            width=width,
            height=height,
            duration_ms=duration_ms,
        )
    )


@router.post('/multipart', summary='Agent 为主人初始化受控分片上传')
async def start_agent_multipart_upload(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    obj: StartMultipartParam,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
) -> ResponseSchemaModel[dict]:
    if obj.category not in _ALLOWED_CATEGORIES:
        raise errors.RequestError(
            code=422,
            msg='STORAGE_CATEGORY_UNSUPPORTED',
            data={'category': obj.category},
        )
    return response_base.success(
        data=await _owner_storage.start_multipart(
            owner_hasn_id=agent.owner_hasn_id,
            declared_size=obj.declared_size,
            filename=obj.filename,
            mime=obj.mime,
            category=obj.category,
            source_app=obj.source_app,
            idempotency_key=idempotency_key,
            parent_entry_id=obj.parent_entry_id,
        )
    )


@router.put('/multipart/{upload_id}/parts/{part_number}', summary='Agent 上传受控 multipart 分片')
async def upload_agent_multipart_part(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    upload_id: str,
    part_number: int,
    file: Annotated[UploadFile, File(description='当前分片')],
    size: Annotated[int, Form(gt=0)],
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await _owner_storage.upload_multipart_part(
            owner_hasn_id=agent.owner_hasn_id,
            upload_id=upload_id,
            part_number=part_number,
            file=file.file,
            size=size,
        )
    )


@router.post('/multipart/{upload_id}/complete', summary='Agent 完成受控 multipart 上传')
async def complete_agent_multipart_upload(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    upload_id: str,
) -> ResponseSchemaModel[UploadedAsset]:
    stored = await _owner_storage.complete_multipart(
        owner_hasn_id=agent.owner_hasn_id,
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


@router.delete('/multipart/{upload_id}', summary='Agent 终止受控 multipart 上传')
async def abort_agent_multipart_upload(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    upload_id: str,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await _owner_storage.abort_multipart(
            owner_hasn_id=agent.owner_hasn_id,
            upload_id=upload_id,
        )
    )
