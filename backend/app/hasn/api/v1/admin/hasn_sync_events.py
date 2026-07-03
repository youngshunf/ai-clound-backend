from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_sync_events import (
    CreateHasnSyncEventsParam,
    DeleteHasnSyncEventsParam,
    GetHasnSyncEventsDetail,
    UpdateHasnSyncEventsParam,
)
from backend.app.hasn.service.hasn_sync_events_service import hasn_sync_events_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 服务端下行同步事件详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_sync_events')
async def get_hasn_sync_events(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 服务端下行同步事件 ID')]
) -> ResponseSchemaModel[GetHasnSyncEventsDetail]:
    hasn_sync_events = await hasn_sync_events_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_sync_events)


@router.get(
    '',
    summary='分页获取所有HASN 服务端下行同步事件',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_sync_eventss_paginated')
async def get_hasn_sync_eventss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnSyncEventsDetail]]:
    page_data = await hasn_sync_events_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 服务端下行同步事件',
    dependencies=[
        Depends(RequestPermission('hasn:sync:events:add')),
        DependsRBAC,
    ],
)
async def create_hasn_sync_events(db: CurrentSessionTransaction, obj: CreateHasnSyncEventsParam) -> ResponseModel:
    await hasn_sync_events_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 服务端下行同步事件',
    dependencies=[
        Depends(RequestPermission('hasn:sync:events:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_sync_events(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 服务端下行同步事件 ID')], obj: UpdateHasnSyncEventsParam
) -> ResponseModel:
    count = await hasn_sync_events_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 服务端下行同步事件',
    dependencies=[
        Depends(RequestPermission('hasn:sync:events:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_sync_eventss(db: CurrentSessionTransaction, obj: DeleteHasnSyncEventsParam) -> ResponseModel:
    count = await hasn_sync_events_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
