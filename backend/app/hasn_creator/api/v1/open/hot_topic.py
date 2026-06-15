"""热榜快照（全局，去重，喂选题；可选数据源） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.hot_topic import GetHotTopicDetail
from backend.app.hasn_creator.service.hot_topic_service import hot_topic_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取热榜快照（全局，去重，喂选题；可选数据源）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_hot_topic',
)
async def get_hot_topic(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHotTopicDetail]]:
    page_data = await hot_topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取热榜快照（全局，去重，喂选题；可选数据源）详情',
    name='hasn_creator_open_get_hot_topic_detail',
)
async def get_hot_topic_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
) -> ResponseSchemaModel[GetHotTopicDetail]:
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    return response_base.success(data=hot_topic)
