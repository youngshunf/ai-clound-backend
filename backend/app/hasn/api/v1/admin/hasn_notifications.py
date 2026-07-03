from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_notifications import (
    CreateHasnNotificationsParam,
    DeleteHasnNotificationsParam,
    GetHasnNotificationsDetail,
    UpdateHasnNotificationsParam,
)
from backend.app.hasn.service.hasn_notifications_service import hasn_notifications_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 通知队列详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_notifications')
async def get_hasn_notifications(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 通知队列 ID')]
) -> ResponseSchemaModel[GetHasnNotificationsDetail]:
    hasn_notifications = await hasn_notifications_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_notifications)


@router.get(
    '',
    summary='分页获取所有HASN 通知队列',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_notificationss_paginated')
async def get_hasn_notificationss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnNotificationsDetail]]:
    page_data = await hasn_notifications_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 通知队列',
    dependencies=[
        Depends(RequestPermission('hasn:notifications:add')),
        DependsRBAC,
    ],
)
async def create_hasn_notifications(db: CurrentSessionTransaction, obj: CreateHasnNotificationsParam) -> ResponseModel:
    await hasn_notifications_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 通知队列',
    dependencies=[
        Depends(RequestPermission('hasn:notifications:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_notifications(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 通知队列 ID')], obj: UpdateHasnNotificationsParam
) -> ResponseModel:
    count = await hasn_notifications_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 通知队列',
    dependencies=[
        Depends(RequestPermission('hasn:notifications:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_notificationss(db: CurrentSessionTransaction, obj: DeleteHasnNotificationsParam) -> ResponseModel:
    count = await hasn_notifications_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
