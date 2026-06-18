from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_designsystem.schema.design_system import (
    CreateDesignSystemParam,
    DeleteDesignSystemParam,
    GetDesignSystemDetail,
    UpdateDesignSystemParam,
)
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取设计系统（云端权威）详情', dependencies=[DependsJwtAuth], name='hasn_designsystem_admin_get_design_system')
async def get_design_system(
    db: CurrentSession, pk: Annotated[int, Path(description='设计系统（云端权威） ID')]
) -> ResponseSchemaModel[GetDesignSystemDetail]:
    design_system = await design_system_service.get(db=db, pk=pk)
    return response_base.success(data=design_system)


@router.get(
    '',
    summary='分页获取所有设计系统（云端权威）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_designsystem_admin_get_design_system_paginated',
)
async def get_design_system_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetDesignSystemDetail]]:
    page_data = await design_system_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计系统（云端权威）',
    dependencies=[
        Depends(RequestPermission('design:system:add')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_create_design_system',
)
async def create_design_system(db: CurrentSessionTransaction, obj: CreateDesignSystemParam) -> ResponseModel:
    await design_system_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新设计系统（云端权威）',
    dependencies=[
        Depends(RequestPermission('design:system:edit')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_update_design_system',
)
async def update_design_system(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='设计系统（云端权威） ID')], obj: UpdateDesignSystemParam
) -> ResponseModel:
    count = await design_system_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除设计系统（云端权威）',
    dependencies=[
        Depends(RequestPermission('design:system:del')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_delete_design_system',
)
async def delete_design_system(db: CurrentSessionTransaction, obj: DeleteDesignSystemParam) -> ResponseModel:
    count = await design_system_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
