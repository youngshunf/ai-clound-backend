from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    GetHasnAppCatalogDetail,
    UpdateHasnAppCatalogParam,
)
from backend.app.hasn.service.hasn_app_catalog_service import hasn_app_catalog_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取AI-Native 应用目录（云端权威）详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_app_catalog')
async def get_hasn_app_catalog(
    db: CurrentSession, pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')]
) -> ResponseSchemaModel[GetHasnAppCatalogDetail]:
    hasn_app_catalog = await hasn_app_catalog_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_app_catalog)


@router.get(
    '',
    summary='分页获取所有AI-Native 应用目录（云端权威）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_app_catalog_paginated',
)
async def get_hasn_app_catalog_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAppCatalogDetail]]:
    page_data = await hasn_app_catalog_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_app_catalog',
)
async def create_hasn_app_catalog(db: CurrentSessionTransaction, obj: CreateHasnAppCatalogParam) -> ResponseModel:
    await hasn_app_catalog_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_app_catalog',
)
async def update_hasn_app_catalog(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')], obj: UpdateHasnAppCatalogParam
) -> ResponseModel:
    count = await hasn_app_catalog_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_app_catalog',
)
async def delete_hasn_app_catalog(db: CurrentSessionTransaction, obj: DeleteHasnAppCatalogParam) -> ResponseModel:
    count = await hasn_app_catalog_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
