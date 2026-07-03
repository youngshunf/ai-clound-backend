from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_agent_runtime_reports import (
    CreateHasnAgentRuntimeReportsParam,
    DeleteHasnAgentRuntimeReportsParam,
    GetHasnAgentRuntimeReportsDetail,
    UpdateHasnAgentRuntimeReportsParam,
)
from backend.app.hasn.service.hasn_agent_runtime_reports_service import hasn_agent_runtime_reports_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Agent Runtime 脱敏摘要上报详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_agent_runtime_reports')
async def get_hasn_agent_runtime_reports(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Agent Runtime 脱敏摘要上报 ID')]
) -> ResponseSchemaModel[GetHasnAgentRuntimeReportsDetail]:
    hasn_agent_runtime_reports = await hasn_agent_runtime_reports_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_agent_runtime_reports)


@router.get(
    '',
    summary='分页获取所有HASN Agent Runtime 脱敏摘要上报',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_agent_runtime_reportss_paginated')
async def get_hasn_agent_runtime_reportss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAgentRuntimeReportsDetail]]:
    page_data = await hasn_agent_runtime_reports_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Agent Runtime 脱敏摘要上报',
    dependencies=[
        Depends(RequestPermission('hasn:agent:runtime:reports:add')),
        DependsRBAC,
    ],
)
async def create_hasn_agent_runtime_reports(db: CurrentSessionTransaction, obj: CreateHasnAgentRuntimeReportsParam) -> ResponseModel:
    await hasn_agent_runtime_reports_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Agent Runtime 脱敏摘要上报',
    dependencies=[
        Depends(RequestPermission('hasn:agent:runtime:reports:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_agent_runtime_reports(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Agent Runtime 脱敏摘要上报 ID')], obj: UpdateHasnAgentRuntimeReportsParam
) -> ResponseModel:
    count = await hasn_agent_runtime_reports_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Agent Runtime 脱敏摘要上报',
    dependencies=[
        Depends(RequestPermission('hasn:agent:runtime:reports:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_agent_runtime_reportss(db: CurrentSessionTransaction, obj: DeleteHasnAgentRuntimeReportsParam) -> ResponseModel:
    count = await hasn_agent_runtime_reports_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
