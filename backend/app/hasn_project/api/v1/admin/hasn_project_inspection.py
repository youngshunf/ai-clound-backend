from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_project.schema.hasn_project_inspection import (
    CreateHasnProjectInspectionParam,
    DeleteHasnProjectInspectionParam,
    GetHasnProjectInspectionDetail,
    UpdateHasnProjectInspectionParam,
)
from backend.app.hasn_project.service.hasn_project_inspection_service import hasn_project_inspection_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）详情', dependencies=[DependsJwtAuth], name='hasn_project_admin_get_hasn_project_inspection')
async def get_hasn_project_inspection(
    db: CurrentSession, pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')]
) -> ResponseSchemaModel[GetHasnProjectInspectionDetail]:
    hasn_project_inspection = await hasn_project_inspection_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_project_inspection)


@router.get(
    '',
    summary='分页获取所有平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_project_admin_get_hasn_project_inspection_paginated',
)
async def get_hasn_project_inspection_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnProjectInspectionDetail]]:
    page_data = await hasn_project_inspection_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[
        Depends(RequestPermission('hasn:project:inspection:add')),
        DependsRBAC,
    ],
    name='hasn_project_admin_create_hasn_project_inspection',
)
async def create_hasn_project_inspection(db: CurrentSessionTransaction, obj: CreateHasnProjectInspectionParam) -> ResponseModel:
    await hasn_project_inspection_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[
        Depends(RequestPermission('hasn:project:inspection:edit')),
        DependsRBAC,
    ],
    name='hasn_project_admin_update_hasn_project_inspection',
)
async def update_hasn_project_inspection(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID')], obj: UpdateHasnProjectInspectionParam
) -> ResponseModel:
    count = await hasn_project_inspection_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
    dependencies=[
        Depends(RequestPermission('hasn:project:inspection:del')),
        DependsRBAC,
    ],
    name='hasn_project_admin_delete_hasn_project_inspection',
)
async def delete_hasn_project_inspection(db: CurrentSessionTransaction, obj: DeleteHasnProjectInspectionParam) -> ResponseModel:
    count = await hasn_project_inspection_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
