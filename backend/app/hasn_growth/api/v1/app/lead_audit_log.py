"""Lead automation PII and compliance audit log - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_audit_log import (
    CreateLeadAuditLogParam,
    GetLeadAuditLogDetail,
    UpdateLeadAuditLogParam,
)
from backend.app.hasn_growth.service.lead_audit_log_service import lead_audit_log_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Lead automation PII and compliance audit log列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_audit_logs(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadAuditLogDetail]]:
    page_data = await lead_audit_log_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead automation PII and compliance audit log',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_audit_log(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadAuditLogParam,
) -> ResponseModel:
    result = await lead_audit_log_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Lead automation PII and compliance audit log详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_audit_log(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')],
) -> ResponseSchemaModel[GetLeadAuditLogDetail]:
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if lead_audit_log.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Lead automation PII and compliance audit log')
    return response_base.success(data=lead_audit_log)


@router.put(
    '/{pk}',
    summary='更新Lead automation PII and compliance audit log',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_audit_log(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')],
    obj: UpdateLeadAuditLogParam,
) -> ResponseModel:
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if getattr(lead_audit_log, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Lead automation PII and compliance audit log')
    count = await lead_audit_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead automation PII and compliance audit log',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_audit_log(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if lead_audit_log.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead automation PII and compliance audit log')
    from backend.app.hasn_growth.schema.lead_audit_log import DeleteLeadAuditLogParam
    count = await lead_audit_log_service.delete(db=db, obj=DeleteLeadAuditLogParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
