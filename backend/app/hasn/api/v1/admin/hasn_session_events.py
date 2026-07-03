from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_session_events import (
    CreateHasnSessionEventsParam,
    DeleteHasnSessionEventsParam,
    GetHasnSessionEventsDetail,
    UpdateHasnSessionEventsParam,
)
from backend.app.hasn.service.hasn_session_events_service import hasn_session_events_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 会话事件详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_session_events')
async def get_hasn_session_events(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 会话事件 ID')]
) -> ResponseSchemaModel[GetHasnSessionEventsDetail]:
    hasn_session_events = await hasn_session_events_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_session_events)


@router.get(
    '',
    summary='分页获取所有HASN 会话事件',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_session_events_paginated',
)
async def get_hasn_session_events_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnSessionEventsDetail]]:
    page_data = await hasn_session_events_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 会话事件',
    dependencies=[
        Depends(RequestPermission('hasn:session:events:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_session_events',
)
async def create_hasn_session_events(db: CurrentSessionTransaction, obj: CreateHasnSessionEventsParam) -> ResponseModel:
    await hasn_session_events_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 会话事件',
    dependencies=[
        Depends(RequestPermission('hasn:session:events:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_session_events',
)
async def update_hasn_session_events(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 会话事件 ID')], obj: UpdateHasnSessionEventsParam
) -> ResponseModel:
    count = await hasn_session_events_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 会话事件',
    dependencies=[
        Depends(RequestPermission('hasn:session:events:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_session_events',
)
async def delete_hasn_session_events(db: CurrentSessionTransaction, obj: DeleteHasnSessionEventsParam) -> ResponseModel:
    count = await hasn_session_events_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
