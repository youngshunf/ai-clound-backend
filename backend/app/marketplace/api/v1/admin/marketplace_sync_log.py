from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    DeleteMarketplaceSyncLogParam,
    GetMarketplaceSyncLogDetail,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.service.marketplace_sync_log_service import marketplace_sync_log_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取技能市场同步日志详情', dependencies=[DependsJwtAuth], name='admin_get_marketplace_sync_log')
async def get_marketplace_sync_log(
    db: CurrentSession, pk: Annotated[int, Path(description='技能市场同步日志 ID')]
) -> ResponseSchemaModel[GetMarketplaceSyncLogDetail]:
    marketplace_sync_log = await marketplace_sync_log_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_sync_log)


@router.get(
    '',
    summary='分页获取所有技能市场同步日志',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_marketplace_sync_log_paginated',
)
async def get_marketplace_sync_log_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceSyncLogDetail]]:
    page_data = await marketplace_sync_log_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建技能市场同步日志',
    dependencies=[
        Depends(RequestPermission('marketplace:sync:log:add')),
        DependsRBAC,
    ],
    name='admin_create_marketplace_sync_log',
)
async def create_marketplace_sync_log(db: CurrentSessionTransaction, obj: CreateMarketplaceSyncLogParam) -> ResponseModel:
    await marketplace_sync_log_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新技能市场同步日志',
    dependencies=[
        Depends(RequestPermission('marketplace:sync:log:edit')),
        DependsRBAC,
    ],
    name='admin_update_marketplace_sync_log',
)
async def update_marketplace_sync_log(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='技能市场同步日志 ID')], obj: UpdateMarketplaceSyncLogParam
) -> ResponseModel:
    count = await marketplace_sync_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除技能市场同步日志',
    dependencies=[
        Depends(RequestPermission('marketplace:sync:log:del')),
        DependsRBAC,
    ],
    name='admin_delete_marketplace_sync_log',
)
async def delete_marketplace_sync_log(db: CurrentSessionTransaction, obj: DeleteMarketplaceSyncLogParam) -> ResponseModel:
    count = await marketplace_sync_log_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
