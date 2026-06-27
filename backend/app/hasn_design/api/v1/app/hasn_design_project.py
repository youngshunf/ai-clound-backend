"""设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_design.schema.hasn_design_project import (
    CreateHasnDesignProjectParam,
    GetHasnDesignProjectDetail,
    UpdateHasnDesignProjectParam,
)
from backend.app.hasn_design.service.hasn_design_project_service import hasn_design_project_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_design_app_get_my_hasn_design_project',
)
async def get_my_hasn_design_project(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnDesignProjectDetail]]:
    page_data = await hasn_design_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsJwtAuth],
    name='hasn_design_app_create_my_hasn_design_project',
)
async def create_my_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnDesignProjectParam,
) -> ResponseModel:
    result = await hasn_design_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_design_app_get_my_hasn_design_project_detail',
)
async def get_my_hasn_design_project_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseSchemaModel[GetHasnDesignProjectDetail]:
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    if hasn_design_project.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    return response_base.success(data=hasn_design_project)


@router.put(
    '/{pk}',
    summary='更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsJwtAuth],
    name='hasn_design_app_update_my_hasn_design_project',
)
async def update_my_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
    obj: UpdateHasnDesignProjectParam,
) -> ResponseModel:
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    if getattr(hasn_design_project, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    count = await hasn_design_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsJwtAuth],
    name='hasn_design_app_delete_my_hasn_design_project',
)
async def delete_my_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    if hasn_design_project.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    from backend.app.hasn_design.schema.hasn_design_project import DeleteHasnDesignProjectParam

    count = await hasn_design_project_service.delete(db=db, obj=DeleteHasnDesignProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
