from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_conversations import (
    CreateHasnConversationsParam,
    DeleteHasnConversationsParam,
    GetHasnConversationsDetail,
    UpdateHasnConversationsParam,
)
from backend.app.hasn.service.hasn_conversations_service import hasn_conversations_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 会话详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_conversations')
async def get_hasn_conversations(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 会话 ID')]
) -> ResponseSchemaModel[GetHasnConversationsDetail]:
    hasn_conversations = await hasn_conversations_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_conversations)


@router.get(
    '',
    summary='分页获取所有HASN 会话',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_conversationss_paginated')
async def get_hasn_conversationss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnConversationsDetail]]:
    page_data = await hasn_conversations_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 会话',
    dependencies=[
        Depends(RequestPermission('hasn:conversations:add')),
        DependsRBAC,
    ],
)
async def create_hasn_conversations(db: CurrentSessionTransaction, obj: CreateHasnConversationsParam) -> ResponseModel:
    await hasn_conversations_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 会话',
    dependencies=[
        Depends(RequestPermission('hasn:conversations:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_conversations(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 会话 ID')], obj: UpdateHasnConversationsParam
) -> ResponseModel:
    count = await hasn_conversations_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 会话',
    dependencies=[
        Depends(RequestPermission('hasn:conversations:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_conversationss(db: CurrentSessionTransaction, obj: DeleteHasnConversationsParam) -> ResponseModel:
    count = await hasn_conversations_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
