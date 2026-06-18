"""设计系统下游消费登记（换系统重渲染追踪） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.consumer_link import (
    CreateConsumerLinkParam,
    GetConsumerLinkDetail,
    UpdateConsumerLinkParam,
)
from backend.app.hasn_designsystem.service.consumer_link_service import consumer_link_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的设计系统下游消费登记（换系统重渲染追踪）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_designsystem_app_get_my_consumer_link',
)
async def get_my_consumer_link(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetConsumerLinkDetail]]:
    page_data = await consumer_link_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_create_my_consumer_link',
)
async def create_my_consumer_link(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateConsumerLinkParam,
) -> ResponseModel:
    result = await consumer_link_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计系统下游消费登记（换系统重渲染追踪）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_get_my_consumer_link_detail',
)
async def get_my_consumer_link_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')],
) -> ResponseSchemaModel[GetConsumerLinkDetail]:
    consumer_link = await consumer_link_service.get(db=db, pk=pk)
    if consumer_link.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该设计系统下游消费登记（换系统重渲染追踪）')
    return response_base.success(data=consumer_link)


@router.put(
    '/{pk}',
    summary='更新设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_update_my_consumer_link',
)
async def update_my_consumer_link(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')],
    obj: UpdateConsumerLinkParam,
) -> ResponseModel:
    consumer_link = await consumer_link_service.get(db=db, pk=pk)
    if getattr(consumer_link, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该设计系统下游消费登记（换系统重渲染追踪）')
    count = await consumer_link_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计系统下游消费登记（换系统重渲染追踪）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_delete_my_consumer_link',
)
async def delete_my_consumer_link(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')],
) -> ResponseModel:
    user_id = request.user.id
    consumer_link = await consumer_link_service.get(db=db, pk=pk)
    if consumer_link.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该设计系统下游消费登记（换系统重渲染追踪）')
    from backend.app.hasn_designsystem.schema.consumer_link import DeleteConsumerLinkParam
    count = await consumer_link_service.delete(db=db, obj=DeleteConsumerLinkParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
