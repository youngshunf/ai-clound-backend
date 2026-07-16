from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_project.schema.hasn_project_milestone import (
    CreateHasnProjectMilestoneParam,
    DeleteHasnProjectMilestoneParam,
    GetHasnProjectMilestoneDetail,
    UpdateHasnProjectMilestoneParam,
)
from backend.app.hasn_project.service.hasn_project_milestone_service import hasn_project_milestone_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）详情', dependencies=[DependsJwtAuth], name='hasn_project_admin_get_hasn_project_milestone')
async def get_hasn_project_milestone(
    db: CurrentSession, pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')]
) -> ResponseSchemaModel[GetHasnProjectMilestoneDetail]:
    hasn_project_milestone = await hasn_project_milestone_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project_milestone)


@router.get(
    '',
    summary='分页获取所有平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_project_admin_get_hasn_project_milestone_paginated',
)
async def get_hasn_project_milestone_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnProjectMilestoneDetail]]:
    page_data = await hasn_project_milestone_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[
        Depends(RequestPermission('hasn:project:milestone:add')),
        DependsRBAC,
    ],
    name='hasn_project_admin_create_hasn_project_milestone',
)
async def create_hasn_project_milestone(db: CurrentSessionTransaction, obj: CreateHasnProjectMilestoneParam) -> ResponseModel:
    await hasn_project_milestone_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[
        Depends(RequestPermission('hasn:project:milestone:edit')),
        DependsRBAC,
    ],
    name='hasn_project_admin_update_hasn_project_milestone',
)
async def update_hasn_project_milestone(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')], obj: UpdateHasnProjectMilestoneParam
) -> ResponseModel:
    count = await hasn_project_milestone_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[
        Depends(RequestPermission('hasn:project:milestone:del')),
        DependsRBAC,
    ],
    name='hasn_project_admin_delete_hasn_project_milestone',
)
async def delete_hasn_project_milestone(db: CurrentSessionTransaction, obj: DeleteHasnProjectMilestoneParam) -> ResponseModel:
    count = await hasn_project_milestone_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
