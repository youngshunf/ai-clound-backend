"""平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_project.schema.hasn_project_milestone import (
    CreateHasnProjectMilestoneParam,
    GetHasnProjectMilestoneDetail,
    UpdateHasnProjectMilestoneParam,
)
from backend.app.hasn_project.service.hasn_project_milestone_service import hasn_project_milestone_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_project_app_get_my_hasn_project_milestone',
)
async def get_my_hasn_project_milestone(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectMilestoneDetail]]:
    page_data = await hasn_project_milestone_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_create_my_hasn_project_milestone',
)
async def create_my_hasn_project_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnProjectMilestoneParam,
) -> ResponseModel:
    result = await hasn_project_milestone_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_get_my_hasn_project_milestone_detail',
)
async def get_my_hasn_project_milestone_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')],
) -> ResponseSchemaModel[GetHasnProjectMilestoneDetail]:
    hasn_project_milestone = await hasn_project_milestone_service.get(db=db, pk=pk)
    if hasn_project_milestone.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）')
    return response_base.success(data=hasn_project_milestone)


@router.put(
    '/{pk}',
    summary='更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_update_my_hasn_project_milestone',
)
async def update_my_hasn_project_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')],
    obj: UpdateHasnProjectMilestoneParam,
) -> ResponseModel:
    hasn_project_milestone = await hasn_project_milestone_service.get(db=db, pk=pk)
    if getattr(hasn_project_milestone, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）')
    count = await hasn_project_milestone_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_delete_my_hasn_project_milestone',
)
async def delete_my_hasn_project_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_project_milestone = await hasn_project_milestone_service.get(db=db, pk=pk)
    if hasn_project_milestone.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）')
    from backend.app.hasn_project.schema.hasn_project_milestone import DeleteHasnProjectMilestoneParam
    count = await hasn_project_milestone_service.delete(db=db, obj=DeleteHasnProjectMilestoneParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
