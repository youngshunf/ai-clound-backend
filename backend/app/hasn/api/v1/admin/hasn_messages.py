from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_messages import (
    CreateHasnMessagesParam,
    DeleteHasnMessagesParam,
    GetHasnMessagesDetail,
    UpdateHasnMessagesParam,
)
from backend.app.hasn.service.hasn_messages_service import hasn_messages_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 消息详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_messages')
async def get_hasn_messages(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 消息 ID')]
) -> ResponseSchemaModel[GetHasnMessagesDetail]:
    hasn_messages = await hasn_messages_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_messages)


@router.get(
    '',
    summary='分页获取所有HASN 消息',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_messagess_paginated')
async def get_hasn_messagess_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnMessagesDetail]]:
    page_data = await hasn_messages_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 消息',
    dependencies=[
        Depends(RequestPermission('hasn:messages:add')),
        DependsRBAC,
    ],
)
async def create_hasn_messages(db: CurrentSessionTransaction, obj: CreateHasnMessagesParam) -> ResponseModel:
    await hasn_messages_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 消息',
    dependencies=[
        Depends(RequestPermission('hasn:messages:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_messages(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 消息 ID')], obj: UpdateHasnMessagesParam
) -> ResponseModel:
    count = await hasn_messages_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 消息',
    dependencies=[
        Depends(RequestPermission('hasn:messages:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_messagess(db: CurrentSessionTransaction, obj: DeleteHasnMessagesParam) -> ResponseModel:
    count = await hasn_messages_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
