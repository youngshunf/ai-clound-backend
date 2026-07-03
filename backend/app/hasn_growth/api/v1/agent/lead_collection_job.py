"""AI lead automation collection job - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_collection_job import (
    CreateLeadCollectionJobParam,
    UpdateLeadCollectionJobParam,
)
from backend.app.hasn_growth.service.lead_collection_job_service import lead_collection_job_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='AI lead automation collection job列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_collection_jobs(
    db: CurrentSession,
    request: Request) -> ResponseModel:

    data = await lead_collection_job_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建AI lead automation collection job',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_collection_job(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadCollectionJobParam) -> ResponseModel:

    result = await lead_collection_job_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取AI lead automation collection job详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_collection_job(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if lead_collection_job.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该AI lead automation collection job')
    return response_base.success(data=lead_collection_job)


@router.put(
    '/{pk}',
    summary='更新AI lead automation collection job',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_collection_job(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')],
    obj: UpdateLeadCollectionJobParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if lead_collection_job.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该AI lead automation collection job')
    count = await lead_collection_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除AI lead automation collection job',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_collection_job(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if lead_collection_job.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该AI lead automation collection job')
    from backend.app.hasn_growth.schema.lead_collection_job import DeleteLeadCollectionJobParam
    count = await lead_collection_job_service.delete(db=db, obj=DeleteLeadCollectionJobParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
