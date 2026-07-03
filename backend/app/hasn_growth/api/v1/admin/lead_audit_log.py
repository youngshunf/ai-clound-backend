from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_audit_log import (
    CreateLeadAuditLogParam,
    DeleteLeadAuditLogParam,
    GetLeadAuditLogDetail,
    UpdateLeadAuditLogParam,
)
from backend.app.hasn_growth.service.lead_audit_log_service import lead_audit_log_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Lead automation PII and compliance audit log详情', dependencies=[DependsJwtAuth], name='admin_get_lead_audit_log')
async def get_lead_audit_log(
    db: CurrentSession, pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')]
) -> ResponseSchemaModel[GetLeadAuditLogDetail]:
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    return response_base.success(data=lead_audit_log)


@router.get(
    '',
    summary='分页获取所有Lead automation PII and compliance audit log',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_audit_logs_paginated')
async def get_lead_audit_logs_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadAuditLogDetail]]:
    page_data = await lead_audit_log_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead automation PII and compliance audit log',
    dependencies=[
        Depends(RequestPermission('lead:audit:log:add')),
        DependsRBAC,
    ],
)
async def create_lead_audit_log(db: CurrentSessionTransaction, obj: CreateLeadAuditLogParam) -> ResponseModel:
    await lead_audit_log_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Lead automation PII and compliance audit log',
    dependencies=[
        Depends(RequestPermission('lead:audit:log:edit')),
        DependsRBAC,
    ],
)
async def update_lead_audit_log(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')], obj: UpdateLeadAuditLogParam
) -> ResponseModel:
    count = await lead_audit_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Lead automation PII and compliance audit log',
    dependencies=[
        Depends(RequestPermission('lead:audit:log:del')),
        DependsRBAC,
    ],
)
async def delete_lead_audit_logs(db: CurrentSessionTransaction, obj: DeleteLeadAuditLogParam) -> ResponseModel:
    count = await lead_audit_log_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
