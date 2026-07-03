from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_audit_log import (
    CreateHasnAuditLogParam,
    DeleteHasnAuditLogParam,
    GetHasnAuditLogDetail,
    UpdateHasnAuditLogParam,
)
from backend.app.hasn.service.hasn_audit_log_service import hasn_audit_log_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 审计日志详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_audit_log')
async def get_hasn_audit_log(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 审计日志 ID')]
) -> ResponseSchemaModel[GetHasnAuditLogDetail]:
    hasn_audit_log = await hasn_audit_log_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_audit_log)


@router.get(
    '',
    summary='分页获取所有HASN 审计日志',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_audit_logs_paginated')
async def get_hasn_audit_logs_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAuditLogDetail]]:
    page_data = await hasn_audit_log_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 审计日志',
    dependencies=[
        Depends(RequestPermission('hasn:audit:log:add')),
        DependsRBAC,
    ],
)
async def create_hasn_audit_log(db: CurrentSessionTransaction, obj: CreateHasnAuditLogParam) -> ResponseModel:
    await hasn_audit_log_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 审计日志',
    dependencies=[
        Depends(RequestPermission('hasn:audit:log:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_audit_log(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 审计日志 ID')], obj: UpdateHasnAuditLogParam
) -> ResponseModel:
    count = await hasn_audit_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 审计日志',
    dependencies=[
        Depends(RequestPermission('hasn:audit:log:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_audit_logs(db: CurrentSessionTransaction, obj: DeleteHasnAuditLogParam) -> ResponseModel:
    count = await hasn_audit_log_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
