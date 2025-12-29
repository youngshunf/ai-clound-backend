"""用户 API Key 管理 API"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from backend.app.llm.schema.user_api_key import (
    CreateUserApiKeyParam,
    CreateUserApiKeyResponse,
    GetUserApiKeyDetail,
    GetUserApiKeyList,
    UpdateUserApiKeyParam,
)
from backend.app.llm.service.api_key_service import api_key_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/admin',
    summary='获取所有 API Keys（管理员）',
    dependencies=[
        Depends(RequestPermission('llm:api-key:list')),
        DependsRBAC,
        DependsPagination,
    ],
)
async def get_all_api_keys(
    db: CurrentSession,
    user_id: Annotated[int | None, Query(description='用户 ID')] = None,
    name: Annotated[str | None, Query(description='Key 名称')] = None,
    status: Annotated[str | None, Query(description='状态')] = None,
) -> ResponseSchemaModel[PageData[GetUserApiKeyList]]:
    page_data = await api_key_service.get_all_keys(db, user_id=user_id, name=name, status=status)
    return response_base.success(data=page_data)


@router.get(
    '',
    summary='获取用户的 API Keys',
    dependencies=[DependsJwtAuth],
)
async def get_user_api_keys(request: Request, db: CurrentSession) -> ResponseSchemaModel[list[GetUserApiKeyList]]:
    user_id = request.user.id
    data = await api_key_service.get_user_keys(db, user_id)
    return response_base.success(data=data)


@router.get(
    '/{pk}',
    summary='获取 API Key 详情',
    dependencies=[DependsJwtAuth],
)
async def get_api_key_detail(request: Request, db: CurrentSession, pk: int) -> ResponseSchemaModel[GetUserApiKeyDetail]:
    user_id = request.user.id
    data = await api_key_service.get_detail(db, pk, user_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建 API Key',
    dependencies=[DependsJwtAuth],
)
async def create_api_key(
    request: Request, db: CurrentSession, obj: CreateUserApiKeyParam
) -> ResponseSchemaModel[CreateUserApiKeyResponse]:
    user_id = request.user.id
    data = await api_key_service.create(db, obj, user_id)
    return response_base.success(data=data)


@router.put(
    '/{pk}',
    summary='更新 API Key',
    dependencies=[DependsJwtAuth],
)
async def update_api_key(
    request: Request, db: CurrentSession, pk: int, obj: UpdateUserApiKeyParam
) -> ResponseSchemaModel:
    user_id = request.user.id
    await api_key_service.update(db, pk, obj, user_id)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除 API Key',
    dependencies=[DependsJwtAuth],
)
async def delete_api_key(request: Request, db: CurrentSession, pk: int) -> ResponseSchemaModel:
    user_id = request.user.id
    await api_key_service.delete(db, pk, user_id)
    return response_base.success()
