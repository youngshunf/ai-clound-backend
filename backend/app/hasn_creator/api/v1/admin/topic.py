from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_creator.schema.topic import (
    CreateTopicParam,
    DeleteTopicParam,
    GetTopicDetail,
    UpdateTopicParam,
)
from backend.app.hasn_creator.service.topic_service import topic_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_topic')
async def get_topic(
    db: CurrentSession, pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')]
) -> ResponseSchemaModel[GetTopicDetail]:
    topic = await topic_service.get(db=db, pk=pk)
    return response_base.success(data=topic)


@router.get(
    '',
    summary='分页获取所有选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_topic_paginated',
)
async def get_topic_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetTopicDetail]]:
    page_data = await topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[
        Depends(RequestPermission('topic:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_topic',
)
async def create_topic(db: CurrentSessionTransaction, obj: CreateTopicParam) -> ResponseModel:
    await topic_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[
        Depends(RequestPermission('topic:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_topic',
)
async def update_topic(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')], obj: UpdateTopicParam
) -> ResponseModel:
    count = await topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[
        Depends(RequestPermission('topic:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_topic',
)
async def delete_topic(db: CurrentSessionTransaction, obj: DeleteTopicParam) -> ResponseModel:
    count = await topic_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
