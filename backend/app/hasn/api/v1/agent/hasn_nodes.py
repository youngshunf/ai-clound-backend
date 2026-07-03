"""HASN Node 主 - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_nodes import (
    CreateHasnNodesParam,
    UpdateHasnNodesParam,
)
from backend.app.hasn.service.hasn_nodes_service import hasn_nodes_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='HASN Node 主列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_hasn_nodess(
    db: CurrentSession,
    request: Request) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    data = await hasn_nodes_service.get_list(db=db, user_id=user_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建HASN Node 主',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_hasn_nodes(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateHasnNodesParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    result = await hasn_nodes_service.create(db=db, obj=obj, user_id=user_id)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN Node 主详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_hasn_nodes(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node 主 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_nodes = await hasn_nodes_service.get(db=db, pk=pk)
    if hasn_nodes.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该HASN Node 主')
    return response_base.success(data=hasn_nodes)


@router.put(
    '/{pk}',
    summary='更新HASN Node 主',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_hasn_nodes(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node 主 ID')],
    obj: UpdateHasnNodesParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_nodes = await hasn_nodes_service.get(db=db, pk=pk)
    if hasn_nodes.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN Node 主')
    count = await hasn_nodes_service.update(db=db, pk=pk, obj=obj, user_id=user_id)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN Node 主',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_hasn_nodes(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node 主 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_nodes = await hasn_nodes_service.get(db=db, pk=pk)
    if hasn_nodes.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN Node 主')
    from backend.app.hasn.schema.hasn_nodes import DeleteHasnNodesParam
    count = await hasn_nodes_service.delete(db=db, obj=DeleteHasnNodesParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
