from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.outreach_message import (
    CreateOutreachMessageParam,
    DeleteOutreachMessageParam,
    GetOutreachMessageDetail,
    UpdateOutreachMessageParam,
)
from backend.app.hasn_growth.service.outreach_message_service import outreach_message_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客触达消息（出/入双向，审批状态机核心表）详情', dependencies=[DependsJwtAuth], name='admin_get_outreach_message')
async def get_outreach_message(
    db: CurrentSession, pk: Annotated[int, Path(description='获客触达消息（出/入双向，审批状态机核心表） ID')]
) -> ResponseSchemaModel[GetOutreachMessageDetail]:
    outreach_message = await outreach_message_service.get(db=db, pk=pk)
    return response_base.success(data=outreach_message)


@router.get(
    '',
    summary='分页获取所有获客触达消息（出/入双向，审批状态机核心表）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_outreach_message_paginated',
)
async def get_outreach_message_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetOutreachMessageDetail]]:
    page_data = await outreach_message_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客触达消息（出/入双向，审批状态机核心表）',
    dependencies=[
        Depends(RequestPermission('outreach:message:add')),
        DependsRBAC,
    ],
    name='admin_create_outreach_message',
)
async def create_outreach_message(db: CurrentSessionTransaction, obj: CreateOutreachMessageParam) -> ResponseModel:
    await outreach_message_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客触达消息（出/入双向，审批状态机核心表）',
    dependencies=[
        Depends(RequestPermission('outreach:message:edit')),
        DependsRBAC,
    ],
    name='admin_update_outreach_message',
)
async def update_outreach_message(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客触达消息（出/入双向，审批状态机核心表） ID')], obj: UpdateOutreachMessageParam
) -> ResponseModel:
    count = await outreach_message_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客触达消息（出/入双向，审批状态机核心表）',
    dependencies=[
        Depends(RequestPermission('outreach:message:del')),
        DependsRBAC,
    ],
    name='admin_delete_outreach_message',
)
async def delete_outreach_message(db: CurrentSessionTransaction, obj: DeleteOutreachMessageParam) -> ResponseModel:
    count = await outreach_message_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
