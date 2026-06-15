"""爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.viral_pattern import GetViralPatternDetail
from backend.app.hasn_creator.service.viral_pattern_service import viral_pattern_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_viral_pattern',
)
async def get_viral_pattern(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetViralPatternDetail]]:
    page_data = await viral_pattern_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）详情',
    name='hasn_creator_open_get_viral_pattern_detail',
)
async def get_viral_pattern_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
) -> ResponseSchemaModel[GetViralPatternDetail]:
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    return response_base.success(data=viral_pattern)
