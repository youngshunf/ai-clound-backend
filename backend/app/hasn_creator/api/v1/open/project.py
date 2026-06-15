"""运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.project import GetProjectDetail
from backend.app.hasn_creator.service.project_service import project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_project',
)
async def get_project(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetProjectDetail]]:
    page_data = await project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度详情',
    name='hasn_creator_open_get_project_detail',
)
async def get_project_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
) -> ResponseSchemaModel[GetProjectDetail]:
    project = await project_service.get(db=db, pk=pk)
    return response_base.success(data=project)
