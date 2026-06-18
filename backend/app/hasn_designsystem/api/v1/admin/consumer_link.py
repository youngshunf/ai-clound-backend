from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_designsystem.schema.consumer_link import (
    CreateConsumerLinkParam,
    DeleteConsumerLinkParam,
    GetConsumerLinkDetail,
    UpdateConsumerLinkParam,
)
from backend.app.hasn_designsystem.service.consumer_link_service import consumer_link_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取设计系统下游消费登记（换系统重渲染追踪）详情', dependencies=[DependsJwtAuth], name='hasn_designsystem_admin_get_consumer_link')
async def get_consumer_link(
    db: CurrentSession, pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')]
) -> ResponseSchemaModel[GetConsumerLinkDetail]:
    consumer_link = await consumer_link_service.get(db=db, pk=pk)
    return response_base.success(data=consumer_link)


@router.get(
    '',
    summary='分页获取所有设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_designsystem_admin_get_consumer_link_paginated',
)
async def get_consumer_link_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetConsumerLinkDetail]]:
    page_data = await consumer_link_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[
        Depends(RequestPermission('consumer:link:add')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_create_consumer_link',
)
async def create_consumer_link(db: CurrentSessionTransaction, obj: CreateConsumerLinkParam) -> ResponseModel:
    await consumer_link_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[
        Depends(RequestPermission('consumer:link:edit')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_update_consumer_link',
)
async def update_consumer_link(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')], obj: UpdateConsumerLinkParam
) -> ResponseModel:
    count = await consumer_link_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[
        Depends(RequestPermission('consumer:link:del')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_delete_consumer_link',
)
async def delete_consumer_link(db: CurrentSessionTransaction, obj: DeleteConsumerLinkParam) -> ResponseModel:
    count = await consumer_link_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
