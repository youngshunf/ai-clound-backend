"""视频项目（统一视频引擎 studio：管线/素材/成品的容器） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_studio.schema.studio_project import GetStudioProjectDetail
from backend.app.hasn_studio.service.studio_project_service import studio_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）列表',
    dependencies=[DependsPagination],
    name='hasn_studio_open_get_studio_project',
)
async def get_studio_project(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioProjectDetail]]:
    page_data = await studio_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）详情',
    name='hasn_studio_open_get_studio_project_detail',
)
async def get_studio_project_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
) -> ResponseSchemaModel[GetStudioProjectDetail]:
    studio_project = await studio_project_service.get(db=db, pk=pk)
    return response_base.success(data=studio_project)
