"""平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_project.schema.hasn_project import GetHasnProjectDetail
from backend.app.hasn_project.service.hasn_project_service import hasn_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）列表',
    dependencies=[DependsPagination],
    name='hasn_project_open_get_hasn_project',
)
async def get_hasn_project(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectDetail]]:
    page_data = await hasn_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）详情',
    name='hasn_project_open_get_hasn_project_detail',
)
async def get_hasn_project_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
) -> ResponseSchemaModel[GetHasnProjectDetail]:
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project)
