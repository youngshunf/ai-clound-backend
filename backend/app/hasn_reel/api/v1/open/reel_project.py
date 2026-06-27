"""短视频项目（reel：一组创作的容器 + 默认创作参数） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_reel.schema.reel_project import GetReelProjectDetail
from backend.app.hasn_reel.service.reel_project_service import reel_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取短视频项目（reel：一组创作的容器 + 默认创作参数）列表',
    dependencies=[DependsPagination],
    name='hasn_reel_open_get_reel_project',
)
async def get_reel_project(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetReelProjectDetail]]:
    page_data = await reel_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取短视频项目（reel：一组创作的容器 + 默认创作参数）详情',
    name='hasn_reel_open_get_reel_project_detail',
)
async def get_reel_project_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
) -> ResponseSchemaModel[GetReelProjectDetail]:
    reel_project = await reel_project_service.get(db=db, pk=pk)
    return response_base.success(data=reel_project)
