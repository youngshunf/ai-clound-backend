"""Lead multi-source evidence - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_contact_source import (
    CreateLeadContactSourceParam,
    UpdateLeadContactSourceParam,
)
from backend.app.hasn_growth.service.lead_contact_source_service import lead_contact_source_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='Lead multi-source evidence列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_contact_sources(
    db: CurrentSession,
    request: Request) -> ResponseModel:

    data = await lead_contact_source_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建Lead multi-source evidence',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_contact_source(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadContactSourceParam) -> ResponseModel:

    result = await lead_contact_source_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Lead multi-source evidence详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_contact_source(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if lead_contact_source.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该Lead multi-source evidence')
    return response_base.success(data=lead_contact_source)


@router.put(
    '/{pk}',
    summary='更新Lead multi-source evidence',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_contact_source(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')],
    obj: UpdateLeadContactSourceParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if lead_contact_source.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该Lead multi-source evidence')
    count = await lead_contact_source_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead multi-source evidence',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_contact_source(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if lead_contact_source.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead multi-source evidence')
    from backend.app.hasn_growth.schema.lead_contact_source import DeleteLeadContactSourceParam
    count = await lead_contact_source_service.delete(db=db, obj=DeleteLeadContactSourceParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
