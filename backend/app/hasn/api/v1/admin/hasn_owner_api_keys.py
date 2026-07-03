from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_owner_api_keys import (
    CreateHasnOwnerApiKeysParam,
    DeleteHasnOwnerApiKeysParam,
    GetHasnOwnerApiKeysDetail,
    UpdateHasnOwnerApiKeysParam,
)
from backend.app.hasn.service.hasn_owner_api_keys_service import hasn_owner_api_keys_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Owner API Key 详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_owner_api_keys')
async def get_hasn_owner_api_keys(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Owner API Key  ID')]
) -> ResponseSchemaModel[GetHasnOwnerApiKeysDetail]:
    hasn_owner_api_keys = await hasn_owner_api_keys_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_owner_api_keys)


@router.get(
    '',
    summary='分页获取所有HASN Owner API Key ',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_owner_api_keyss_paginated')
async def get_hasn_owner_api_keyss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnOwnerApiKeysDetail]]:
    page_data = await hasn_owner_api_keys_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Owner API Key ',
    dependencies=[
        Depends(RequestPermission('hasn:owner:api:keys:add')),
        DependsRBAC,
    ],
)
async def create_hasn_owner_api_keys(db: CurrentSessionTransaction, obj: CreateHasnOwnerApiKeysParam) -> ResponseModel:
    await hasn_owner_api_keys_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Owner API Key ',
    dependencies=[
        Depends(RequestPermission('hasn:owner:api:keys:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_owner_api_keys(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Owner API Key  ID')], obj: UpdateHasnOwnerApiKeysParam
) -> ResponseModel:
    count = await hasn_owner_api_keys_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Owner API Key ',
    dependencies=[
        Depends(RequestPermission('hasn:owner:api:keys:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_owner_api_keyss(db: CurrentSessionTransaction, obj: DeleteHasnOwnerApiKeysParam) -> ResponseModel:
    count = await hasn_owner_api_keys_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
