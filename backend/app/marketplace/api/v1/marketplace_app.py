from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.marketplace.schema.marketplace_app import (
    CreateMarketplaceAppParam,
    DeleteMarketplaceAppParam,
    GetMarketplaceAppDetail,
    UpdateMarketplaceAppParam,
)
from backend.app.marketplace.service.marketplace_app_service import marketplace_app_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取技能市场应用详情', dependencies=[DependsJwtAuth])
async def get_marketplace_app(
    db: CurrentSession, pk: Annotated[int, Path(description='技能市场应用 ID')]
) -> ResponseSchemaModel[GetMarketplaceAppDetail]:
    marketplace_app = await marketplace_app_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_app)


@router.get(
    '',
    summary='分页获取所有技能市场应用',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_marketplace_apps_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceAppDetail]]:
    page_data = await marketplace_app_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建技能市场应用',
    dependencies=[
        Depends(RequestPermission('marketplace:app:add')),
        DependsRBAC,
    ],
)
async def create_marketplace_app(db: CurrentSessionTransaction, obj: CreateMarketplaceAppParam) -> ResponseModel:
    await marketplace_app_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新技能市场应用',
    dependencies=[
        Depends(RequestPermission('marketplace:app:edit')),
        DependsRBAC,
    ],
)
async def update_marketplace_app(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='技能市场应用 ID')], obj: UpdateMarketplaceAppParam
) -> ResponseModel:
    count = await marketplace_app_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除技能市场应用',
    dependencies=[
        Depends(RequestPermission('marketplace:app:del')),
        DependsRBAC,
    ],
)
async def delete_marketplace_apps(db: CurrentSessionTransaction, obj: DeleteMarketplaceAppParam) -> ResponseModel:
    count = await marketplace_app_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
