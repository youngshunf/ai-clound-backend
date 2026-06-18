"""设计系统（云端权威） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.design_system import (
    CreateDesignSystemParam,
    GetDesignSystemDetail,
    UpdateDesignSystemParam,
)
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的设计系统（云端权威）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_designsystem_app_get_my_design_system',
)
async def get_my_design_system(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetDesignSystemDetail]]:
    page_data = await design_system_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计系统（云端权威）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_create_my_design_system',
)
async def create_my_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateDesignSystemParam,
) -> ResponseModel:
    result = await design_system_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计系统（云端权威）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_get_my_design_system_detail',
)
async def get_my_design_system_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
) -> ResponseSchemaModel[GetDesignSystemDetail]:
    design_system = await design_system_service.get(db=db, pk=pk)
    if design_system.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该设计系统（云端权威）')
    return response_base.success(data=design_system)


@router.put(
    '/{pk}',
    summary='更新设计系统（云端权威）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_update_my_design_system',
)
async def update_my_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
    obj: UpdateDesignSystemParam,
) -> ResponseModel:
    design_system = await design_system_service.get(db=db, pk=pk)
    if getattr(design_system, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该设计系统（云端权威）')
    count = await design_system_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计系统（云端权威）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_delete_my_design_system',
)
async def delete_my_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
) -> ResponseModel:
    user_id = request.user.id
    design_system = await design_system_service.get(db=db, pk=pk)
    if design_system.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该设计系统（云端权威）')
    from backend.app.hasn_designsystem.schema.design_system import DeleteDesignSystemParam
    count = await design_system_service.delete(db=db, obj=DeleteDesignSystemParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
