from typing import Annotated

from fastapi import APIRouter, Depends

from backend.app.hasn.schema.hasn_api_keys import ApiKeyOut, CreateApiKeyReq
from backend.app.hasn.service.hasn_api_key_service import hasn_api_key_service
from backend.app.hasn.service.hasn_auth import hasn_auth_from_jwt
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/api-keys', summary='获取节点 API Key 列表')
async def list_hasn_api_keys(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    clients = await hasn_api_key_service.list_api_keys(
        db=db,
        user_hasn_id=auth['hasn_id'],
    )
    return response_base.success(data=[
        ApiKeyOut(
            key_id=c.key_id,
            key_name=c.key_name,
            owner_id=c.owner_id,
            status=c.status,
            scopes=c.scopes,
            bound_node_id=c.bound_node_id,
            expires_at=c.expires_at,
            created_time=c.created_time,
            last_seen_at=c.last_used_at,
        ).model_dump()
        for c in clients
    ])


@router.post('/api-keys', summary='生成新的 API Key')
async def create_hasn_api_key(
    obj_in: CreateApiKeyReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    result = await hasn_api_key_service.create_api_key(
        db=db,
        user_id=auth['user_id'],
        user_hasn_id=auth['hasn_id'],
        name=obj_in.name,
        scopes=obj_in.scopes,
        bound_node_id=obj_in.bound_node_id,
        expires_at=obj_in.expires_at,
    )
    await db.commit()
    return response_base.success(data=result.model_dump())


@router.delete('/api-keys/{key_id}', summary='删除(吊销) API Key')
async def delete_hasn_api_key(
    key_id: str,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    await hasn_api_key_service.delete_api_key(
        db=db,
        user_hasn_id=auth['hasn_id'],
        key_id=key_id,
    )
    await db.commit()
    return response_base.success()
