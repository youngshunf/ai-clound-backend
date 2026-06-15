"""选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.topic import (
    CreateTopicParam,
    GetTopicDetail,
    UpdateTopicParam,
)
from backend.app.hasn_creator.service.topic_service import topic_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_topic',
)
async def get_my_topic(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetTopicDetail]]:
    page_data = await topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_topic',
)
async def create_my_topic(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateTopicParam,
) -> ResponseModel:
    result = await topic_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_topic_detail',
)
async def get_my_topic_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
) -> ResponseSchemaModel[GetTopicDetail]:
    topic = await topic_service.get(db=db, pk=pk)
    if topic.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    return response_base.success(data=topic)


@router.put(
    '/{pk}',
    summary='更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_topic',
)
async def update_my_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
    obj: UpdateTopicParam,
) -> ResponseModel:
    topic = await topic_service.get(db=db, pk=pk)
    if getattr(topic, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    count = await topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_topic',
)
async def delete_my_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
) -> ResponseModel:
    user_id = request.user.id
    topic = await topic_service.get(db=db, pk=pk)
    if topic.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    from backend.app.hasn_creator.schema.topic import DeleteTopicParam
    count = await topic_service.delete(db=db, obj=DeleteTopicParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
