"""用户 API Key 管理 API"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from backend.app.newapi.apikey.schema import (
    AdminCreateUserApiKeyParam,
    CreateUserApiKeyParam,
    CreateUserApiKeyResponse,
    GetUserApiKeyDetail,
    GetUserApiKeyList,
    UpdateUserApiKeyParam,
)
from backend.app.newapi.apikey.service import api_key_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

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
    user_keyword: Annotated[str | None, Query(description='用户昵称/手机号搜索')] = None,
) -> ResponseSchemaModel[PageData[GetUserApiKeyList]]:
    page_data = await api_key_service.get_all_keys(
        db,
        user_id=user_id,
        name=name,
        status=status,
        user_keyword=user_keyword,
    )
    return response_base.success(data=PageData[GetUserApiKeyList].model_validate(page_data))


@router.get(
    '/admin/{pk}/full-key',
    summary='获取完整 API Key（管理员）',
    dependencies=[
        Depends(RequestPermission('llm:api-key:list')),
        DependsRBAC,
    ],
)
async def get_full_api_key(db: CurrentSession, pk: int) -> ResponseSchemaModel:
    from backend.common.security.encryption import key_encryption

    api_key = await api_key_service.get(db, pk)
    try:
        full_key = key_encryption.decrypt(api_key.key_encrypted)
    except Exception:
        from backend.common.exception import errors

        raise errors.ServerError(msg='API Key 解密失败')
    return response_base.success(data={'api_key': full_key})


@router.post(
    '/admin',
    summary='管理员为指定用户创建 API Key',
    dependencies=[
        Depends(RequestPermission('llm:api-key:list')),
        DependsRBAC,
    ],
)
async def admin_create_api_key(
    db: CurrentSessionTransaction, obj: AdminCreateUserApiKeyParam
) -> ResponseSchemaModel[CreateUserApiKeyResponse]:
    data = await api_key_service.create(
        db,
        CreateUserApiKeyParam.model_validate(obj.model_dump(exclude={'user_id'})),
        obj.user_id,
    )
    return response_base.success(data=data)


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
    request: Request, db: CurrentSessionTransaction, obj: CreateUserApiKeyParam
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
    request: Request, db: CurrentSessionTransaction, pk: int, obj: UpdateUserApiKeyParam
) -> ResponseModel:
    user_id = request.user.id
    is_admin = request.user.is_superuser
    await api_key_service.update(db, pk, obj, user_id, is_admin)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除 API Key',
    dependencies=[DependsJwtAuth],
)
async def delete_api_key(request: Request, db: CurrentSessionTransaction, pk: int) -> ResponseModel:
    user_id = request.user.id
    is_admin = request.user.is_superuser
    await api_key_service.delete(db, pk, user_id, is_admin)
    return response_base.success()
