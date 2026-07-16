from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_project.schema.hasn_project import (
    CreateHasnProjectParam,
    DeleteHasnProjectParam,
    GetHasnProjectDetail,
    UpdateHasnProjectParam,
)
from backend.app.hasn_project.service.hasn_project_service import hasn_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）详情', dependencies=[DependsJwtAuth], name='hasn_project_admin_get_hasn_project')
async def get_hasn_project(
    db: CurrentSession, pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')]
) -> ResponseSchemaModel[GetHasnProjectDetail]:
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project)


@router.get(
    '',
    summary='分页获取所有平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_project_admin_get_hasn_project_paginated',
)
async def get_hasn_project_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnProjectDetail]]:
    page_data = await hasn_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[
        Depends(RequestPermission('hasn:project:add')),
        DependsRBAC,
    ],
    name='hasn_project_admin_create_hasn_project',
)
async def create_hasn_project(db: CurrentSessionTransaction, obj: CreateHasnProjectParam) -> ResponseModel:
    await hasn_project_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[
        Depends(RequestPermission('hasn:project:edit')),
        DependsRBAC,
    ],
    name='hasn_project_admin_update_hasn_project',
)
async def update_hasn_project(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')], obj: UpdateHasnProjectParam
) -> ResponseModel:
    count = await hasn_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[
        Depends(RequestPermission('hasn:project:del')),
        DependsRBAC,
    ],
    name='hasn_project_admin_delete_hasn_project',
)
async def delete_hasn_project(db: CurrentSessionTransaction, obj: DeleteHasnProjectParam) -> ResponseModel:
    count = await hasn_project_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
