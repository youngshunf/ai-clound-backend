"""选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.topic import GetTopicDetail
from backend.app.hasn_creator.service.topic_service import topic_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_topic',
)
async def get_topic(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetTopicDetail]]:
    page_data = await topic_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过详情',
    name='hasn_creator_open_get_topic_detail',
)
async def get_topic_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
) -> ResponseSchemaModel[GetTopicDetail]:
    topic = await topic_service.get(db=db, pk=pk)
    return response_base.success(data=topic)
