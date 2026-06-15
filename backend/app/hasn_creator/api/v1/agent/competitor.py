"""竞品账号（定位/选题调研输入） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.competitor import (
    CreateCompetitorParam,
    UpdateCompetitorParam,
)
from backend.app.hasn_creator.service.competitor_service import competitor_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='竞品账号（定位/选题调研输入）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_competitor',
)
async def agent_list_competitor(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await competitor_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建竞品账号（定位/选题调研输入）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_competitor',
)
async def agent_create_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateCompetitorParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await competitor_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取竞品账号（定位/选题调研输入）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_competitor',
)
async def agent_get_competitor(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    competitor = await competitor_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if competitor.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该竞品账号（定位/选题调研输入）')
    return response_base.success(data=competitor)


@router.put(
    '/{pk}',
    summary='更新竞品账号（定位/选题调研输入）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_competitor',
)
async def agent_update_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
    obj: UpdateCompetitorParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    competitor = await competitor_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if competitor.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该竞品账号（定位/选题调研输入）')
    count = await competitor_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除竞品账号（定位/选题调研输入）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_competitor',
)
async def agent_delete_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    competitor = await competitor_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if competitor.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该竞品账号（定位/选题调研输入）')
    from backend.app.hasn_creator.schema.competitor import DeleteCompetitorParam
    count = await competitor_service.delete(db=db, obj=DeleteCompetitorParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
