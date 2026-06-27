from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_design.schema.hasn_design_project import (
    CreateHasnDesignProjectParam,
    DeleteHasnDesignProjectParam,
    GetHasnDesignProjectDetail,
    UpdateHasnDesignProjectParam,
)
from backend.app.hasn_design.service.hasn_design_project_service import hasn_design_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/{pk}',
    summary='获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_design_admin_get_hasn_design_project',
)
async def get_hasn_design_project(
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseSchemaModel[GetHasnDesignProjectDetail]:
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_design_project)


@router.get(
    '',
    summary='分页获取所有设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_design_admin_get_hasn_design_project_paginated',
)
async def get_hasn_design_project_paginated(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnDesignProjectDetail]]:
    page_data = await hasn_design_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[
        Depends(RequestPermission('hasn:design:project:add')),
        DependsRBAC,
    ],
    name='hasn_design_admin_create_hasn_design_project',
)
async def create_hasn_design_project(db: CurrentSessionTransaction, obj: CreateHasnDesignProjectParam) -> ResponseModel:
    await hasn_design_project_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[
        Depends(RequestPermission('hasn:design:project:edit')),
        DependsRBAC,
    ],
    name='hasn_design_admin_update_hasn_design_project',
)
async def update_hasn_design_project(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
    obj: UpdateHasnDesignProjectParam,
) -> ResponseModel:
    count = await hasn_design_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[
        Depends(RequestPermission('hasn:design:project:del')),
        DependsRBAC,
    ],
    name='hasn_design_admin_delete_hasn_design_project',
)
async def delete_hasn_design_project(db: CurrentSessionTransaction, obj: DeleteHasnDesignProjectParam) -> ResponseModel:
    count = await hasn_design_project_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
