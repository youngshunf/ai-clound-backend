"""Firecrawl request audit for AI lead automation - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_firecrawl_request import (
    CreateLeadFirecrawlRequestParam,
    UpdateLeadFirecrawlRequestParam,
)
from backend.app.hasn_growth.service.lead_firecrawl_request_service import lead_firecrawl_request_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

@router.get(
    '',
    summary='Firecrawl request audit for AI lead automation列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_firecrawl_requests(
    db: CurrentSession,
    request: Request) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    data = await lead_firecrawl_request_service.get_list(db=db)
    return response_base.success(data=data)

@router.post(
    '',
    summary='创建Firecrawl request audit for AI lead automation',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_firecrawl_request(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadFirecrawlRequestParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    result = await lead_firecrawl_request_service.create(db=db, obj=obj)
    return response_base.success(data=result)

@router.get(
    '/{pk}',
    summary='获取Firecrawl request audit for AI lead automation详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_firecrawl_request(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if lead_firecrawl_request.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该Firecrawl request audit for AI lead automation')
    return response_base.success(data=lead_firecrawl_request)

@router.put(
    '/{pk}',
    summary='更新Firecrawl request audit for AI lead automation',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_firecrawl_request(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')],
    obj: UpdateLeadFirecrawlRequestParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if lead_firecrawl_request.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该Firecrawl request audit for AI lead automation')
    count = await lead_firecrawl_request_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

@router.delete(
    '/{pk}',
    summary='删除Firecrawl request audit for AI lead automation',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_firecrawl_request(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if lead_firecrawl_request.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Firecrawl request audit for AI lead automation')
    from backend.app.hasn_growth.schema.lead_firecrawl_request import DeleteLeadFirecrawlRequestParam
    count = await lead_firecrawl_request_service.delete(db=db, obj=DeleteLeadFirecrawlRequestParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
