"""平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_project.schema.hasn_project_inspection import (
    CreateHasnProjectInspectionParam,
    GetHasnProjectInspectionDetail,
    UpdateHasnProjectInspectionParam,
)
from backend.app.hasn_project.service.hasn_project_inspection_service import hasn_project_inspection_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_project_app_get_my_hasn_project_inspection',
)
async def get_my_hasn_project_inspection(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectInspectionDetail]]:
    page_data = await hasn_project_inspection_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_create_my_hasn_project_inspection',
)
async def create_my_hasn_project_inspection(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnProjectInspectionParam,
) -> ResponseModel:
    result = await hasn_project_inspection_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_get_my_hasn_project_inspection_detail',
)
async def get_my_hasn_project_inspection_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')],
) -> ResponseSchemaModel[GetHasnProjectInspectionDetail]:
    hasn_project_inspection = await hasn_project_inspection_service.get(db=db, pk=pk)
    if hasn_project_inspection.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）')
    return response_base.success(data=hasn_project_inspection)


@router.put(
    '/{pk}',
    summary='更新平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_update_my_hasn_project_inspection',
)
async def update_my_hasn_project_inspection(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')],
    obj: UpdateHasnProjectInspectionParam,
) -> ResponseModel:
    hasn_project_inspection = await hasn_project_inspection_service.get(db=db, pk=pk)
    if getattr(hasn_project_inspection, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）')
    count = await hasn_project_inspection_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_delete_my_hasn_project_inspection',
)
async def delete_my_hasn_project_inspection(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_project_inspection = await hasn_project_inspection_service.get(db=db, pk=pk)
    if hasn_project_inspection.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）')
    from backend.app.hasn_project.schema.hasn_project_inspection import DeleteHasnProjectInspectionParam
    count = await hasn_project_inspection_service.delete(db=db, obj=DeleteHasnProjectInspectionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
