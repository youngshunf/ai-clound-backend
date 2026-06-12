"""AI lead automation source configuration - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_source_config import (
    CreateLeadSourceConfigParam,
    UpdateLeadSourceConfigParam,
)
from backend.app.hasn_growth.service.lead_source_config_service import lead_source_config_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

@router.get(
    '',
    summary='AI lead automation source configuration列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_source_configs(
    db: CurrentSession,
    request: Request) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    data = await lead_source_config_service.get_list(db=db)
    return response_base.success(data=data)

@router.post(
    '',
    summary='创建AI lead automation source configuration',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_source_config(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadSourceConfigParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    result = await lead_source_config_service.create(db=db, obj=obj)
    return response_base.success(data=result)

@router.get(
    '/{pk}',
    summary='获取AI lead automation source configuration详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_source_config(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if lead_source_config.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该AI lead automation source configuration')
    return response_base.success(data=lead_source_config)

@router.put(
    '/{pk}',
    summary='更新AI lead automation source configuration',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_source_config(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')],
    obj: UpdateLeadSourceConfigParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if lead_source_config.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该AI lead automation source configuration')
    count = await lead_source_config_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

@router.delete(
    '/{pk}',
    summary='删除AI lead automation source configuration',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_source_config(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if lead_source_config.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该AI lead automation source configuration')
    from backend.app.hasn_growth.schema.lead_source_config import DeleteLeadSourceConfigParam
    count = await lead_source_config_service.delete(db=db, obj=DeleteLeadSourceConfigParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
