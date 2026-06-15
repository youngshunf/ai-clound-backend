"""内容洞察（复盘结构化结论，进化沉淀核心） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.content_insight import GetContentInsightDetail
from backend.app.hasn_creator.service.content_insight_service import content_insight_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取内容洞察（复盘结构化结论，进化沉淀核心）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_content_insight',
)
async def get_content_insight(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentInsightDetail]]:
    page_data = await content_insight_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取内容洞察（复盘结构化结论，进化沉淀核心）详情',
    name='hasn_creator_open_get_content_insight_detail',
)
async def get_content_insight_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
) -> ResponseSchemaModel[GetContentInsightDetail]:
    content_insight = await content_insight_service.get(db=db, pk=pk)
    return response_base.success(data=content_insight)
