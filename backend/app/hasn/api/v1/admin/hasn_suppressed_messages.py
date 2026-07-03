from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_suppressed_messages import (
    CreateHasnSuppressedMessagesParam,
    DeleteHasnSuppressedMessagesParam,
    GetHasnSuppressedMessagesDetail,
    UpdateHasnSuppressedMessagesParam,
)
from backend.app.hasn.service.hasn_suppressed_messages_service import hasn_suppressed_messages_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Runtime 抑制箱 / owner 可拉取消息详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_suppressed_messages')
async def get_hasn_suppressed_messages(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Runtime 抑制箱 / owner 可拉取消息 ID')]
) -> ResponseSchemaModel[GetHasnSuppressedMessagesDetail]:
    hasn_suppressed_messages = await hasn_suppressed_messages_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_suppressed_messages)


@router.get(
    '',
    summary='分页获取所有HASN Runtime 抑制箱 / owner 可拉取消息',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_suppressed_messagess_paginated')
async def get_hasn_suppressed_messagess_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnSuppressedMessagesDetail]]:
    page_data = await hasn_suppressed_messages_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Runtime 抑制箱 / owner 可拉取消息',
    dependencies=[
        Depends(RequestPermission('hasn:suppressed:messages:add')),
        DependsRBAC,
    ],
)
async def create_hasn_suppressed_messages(db: CurrentSessionTransaction, obj: CreateHasnSuppressedMessagesParam) -> ResponseModel:
    await hasn_suppressed_messages_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Runtime 抑制箱 / owner 可拉取消息',
    dependencies=[
        Depends(RequestPermission('hasn:suppressed:messages:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_suppressed_messages(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Runtime 抑制箱 / owner 可拉取消息 ID')], obj: UpdateHasnSuppressedMessagesParam
) -> ResponseModel:
    count = await hasn_suppressed_messages_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Runtime 抑制箱 / owner 可拉取消息',
    dependencies=[
        Depends(RequestPermission('hasn:suppressed:messages:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_suppressed_messagess(db: CurrentSessionTransaction, obj: DeleteHasnSuppressedMessagesParam) -> ResponseModel:
    count = await hasn_suppressed_messages_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
