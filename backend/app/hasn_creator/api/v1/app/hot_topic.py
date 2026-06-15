"""热榜快照（全局，去重，喂选题；可选数据源） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.hot_topic import (
    CreateHotTopicParam,
    GetHotTopicDetail,
    UpdateHotTopicParam,
)
from backend.app.hasn_creator.service.hot_topic_service import hot_topic_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的热榜快照（全局，去重，喂选题；可选数据源）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_hot_topic',
)
async def get_my_hot_topic(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHotTopicDetail]]:
    page_data = await hot_topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_hot_topic',
)
async def create_my_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHotTopicParam,
) -> ResponseModel:
    result = await hot_topic_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取热榜快照（全局，去重，喂选题；可选数据源）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_hot_topic_detail',
)
async def get_my_hot_topic_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
) -> ResponseSchemaModel[GetHotTopicDetail]:
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    if hot_topic.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该热榜快照（全局，去重，喂选题；可选数据源）')
    return response_base.success(data=hot_topic)


@router.put(
    '/{pk}',
    summary='更新热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_hot_topic',
)
async def update_my_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
    obj: UpdateHotTopicParam,
) -> ResponseModel:
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    if getattr(hot_topic, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该热榜快照（全局，去重，喂选题；可选数据源）')
    count = await hot_topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_hot_topic',
)
async def delete_my_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
) -> ResponseModel:
    user_id = request.user.id
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    if hot_topic.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该热榜快照（全局，去重，喂选题；可选数据源）')
    from backend.app.hasn_creator.schema.hot_topic import DeleteHotTopicParam
    count = await hot_topic_service.delete(db=db, obj=DeleteHotTopicParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
