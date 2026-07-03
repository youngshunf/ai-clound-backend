"""Lead automation PII and compliance audit log - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_audit_log import (
    CreateLeadAuditLogParam,
    UpdateLeadAuditLogParam,
)
from backend.app.hasn_growth.service.lead_audit_log_service import lead_audit_log_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='Lead automation PII and compliance audit log列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_audit_logs(
    db: CurrentSession,
    request: Request) -> ResponseModel:

    data = await lead_audit_log_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建Lead automation PII and compliance audit log',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_audit_log(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadAuditLogParam) -> ResponseModel:

    result = await lead_audit_log_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Lead automation PII and compliance audit log详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_audit_log(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if lead_audit_log.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该Lead automation PII and compliance audit log')
    return response_base.success(data=lead_audit_log)


@router.put(
    '/{pk}',
    summary='更新Lead automation PII and compliance audit log',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_audit_log(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')],
    obj: UpdateLeadAuditLogParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if lead_audit_log.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该Lead automation PII and compliance audit log')
    count = await lead_audit_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead automation PII and compliance audit log',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_audit_log(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead automation PII and compliance audit log ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_audit_log = await lead_audit_log_service.get(db=db, pk=pk)
    if lead_audit_log.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead automation PII and compliance audit log')
    from backend.app.hasn_growth.schema.lead_audit_log import DeleteLeadAuditLogParam
    count = await lead_audit_log_service.delete(db=db, obj=DeleteLeadAuditLogParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
