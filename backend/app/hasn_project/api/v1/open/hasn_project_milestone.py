"""平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_project.schema.hasn_project_milestone import GetHasnProjectMilestoneDetail
from backend.app.hasn_project.service.hasn_project_milestone_service import hasn_project_milestone_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）列表',
    dependencies=[DependsPagination],
    name='hasn_project_open_get_hasn_project_milestone',
)
async def get_hasn_project_milestone(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectMilestoneDetail]]:
    page_data = await hasn_project_milestone_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）详情',
    name='hasn_project_open_get_hasn_project_milestone_detail',
)
async def get_hasn_project_milestone_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')],
) -> ResponseSchemaModel[GetHasnProjectMilestoneDetail]:
    hasn_project_milestone = await hasn_project_milestone_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project_milestone)
