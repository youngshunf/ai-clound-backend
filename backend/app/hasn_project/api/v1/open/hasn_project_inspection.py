"""平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_project.schema.hasn_project_inspection import GetHasnProjectInspectionDetail
from backend.app.hasn_project.service.hasn_project_inspection_service import hasn_project_inspection_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）列表',
    dependencies=[DependsPagination],
    name='hasn_project_open_get_hasn_project_inspection',
)
async def get_hasn_project_inspection(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectInspectionDetail]]:
    page_data = await hasn_project_inspection_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）详情',
    name='hasn_project_open_get_hasn_project_inspection_detail',
)
async def get_hasn_project_inspection_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')],
) -> ResponseSchemaModel[GetHasnProjectInspectionDetail]:
    hasn_project_inspection = await hasn_project_inspection_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project_inspection)
