"""平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_project.schema.hasn_project import (
    CreateHasnProjectParam,
    GetHasnProjectDetail,
    UpdateHasnProjectParam,
)
from backend.app.hasn_project.service.hasn_project_service import hasn_project_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_project_app_get_my_hasn_project',
)
async def get_my_hasn_project(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnProjectDetail]]:
    page_data = await hasn_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_create_my_hasn_project',
)
async def create_my_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnProjectParam,
) -> ResponseModel:
    result = await hasn_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_get_my_hasn_project_detail',
)
async def get_my_hasn_project_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
) -> ResponseSchemaModel[GetHasnProjectDetail]:
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    if hasn_project.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    return response_base.success(data=hasn_project)


@router.put(
    '/{pk}',
    summary='更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_update_my_hasn_project',
)
async def update_my_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
    obj: UpdateHasnProjectParam,
) -> ResponseModel:
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    if getattr(hasn_project, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    count = await hasn_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsJwtAuth],
    name='hasn_project_app_delete_my_hasn_project',
)
async def delete_my_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    if hasn_project.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    from backend.app.hasn_project.schema.hasn_project import DeleteHasnProjectParam
    count = await hasn_project_service.delete(db=db, obj=DeleteHasnProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
