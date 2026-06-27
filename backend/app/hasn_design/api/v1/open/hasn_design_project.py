"""设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） - 公开 API

认证方式: 无（公开接口，无需登录）
"""

from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_design.schema.hasn_design_project import GetHasnDesignProjectDetail
from backend.app.hasn_design.service.hasn_design_project_service import hasn_design_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）列表',
    dependencies=[DependsPagination],
    name='hasn_design_open_get_hasn_design_project',
)
async def get_hasn_design_project(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnDesignProjectDetail]]:
    page_data = await hasn_design_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）详情',
    name='hasn_design_open_get_hasn_design_project_detail',
)
async def get_hasn_design_project_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseSchemaModel[GetHasnDesignProjectDetail]:
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_design_project)
