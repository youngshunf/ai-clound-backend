from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_creator.schema.hot_topic import (
    CreateHotTopicParam,
    DeleteHotTopicParam,
    GetHotTopicDetail,
    UpdateHotTopicParam,
)
from backend.app.hasn_creator.service.hot_topic_service import hot_topic_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取热榜快照（全局，去重，喂选题；可选数据源）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_hot_topic')
async def get_hot_topic(
    db: CurrentSession, pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')]
) -> ResponseSchemaModel[GetHotTopicDetail]:
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    return response_base.success(data=hot_topic)


@router.get(
    '',
    summary='分页获取所有热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_hot_topic_paginated',
)
async def get_hot_topic_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHotTopicDetail]]:
    page_data = await hot_topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[
        Depends(RequestPermission('hot:topic:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_hot_topic',
)
async def create_hot_topic(db: CurrentSessionTransaction, obj: CreateHotTopicParam) -> ResponseModel:
    await hot_topic_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[
        Depends(RequestPermission('hot:topic:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_hot_topic',
)
async def update_hot_topic(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')], obj: UpdateHotTopicParam
) -> ResponseModel:
    count = await hot_topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[
        Depends(RequestPermission('hot:topic:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_hot_topic',
)
async def delete_hot_topic(db: CurrentSessionTransaction, obj: DeleteHotTopicParam) -> ResponseModel:
    count = await hot_topic_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
