"""竞品账号（定位/选题调研输入） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.competitor import GetCompetitorDetail
from backend.app.hasn_creator.service.competitor_service import competitor_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取竞品账号（定位/选题调研输入）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_competitor',
)
async def get_competitor(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetCompetitorDetail]]:
    page_data = await competitor_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取竞品账号（定位/选题调研输入）详情',
    name='hasn_creator_open_get_competitor_detail',
)
async def get_competitor_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
) -> ResponseSchemaModel[GetCompetitorDetail]:
    competitor = await competitor_service.get(db=db, pk=pk)
    return response_base.success(data=competitor)
