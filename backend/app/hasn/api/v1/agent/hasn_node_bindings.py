"""HASN Node Owner Binding 租约 - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_node_bindings import (
    CreateHasnNodeBindingsParam,
    UpdateHasnNodeBindingsParam,
)
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='HASN Node Owner Binding 租约列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_hasn_node_bindingss(
    db: CurrentSession,
    request: Request) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    data = await hasn_node_bindings_service.get_list(db=db, user_id=user_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建HASN Node Owner Binding 租约',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_hasn_node_bindings(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateHasnNodeBindingsParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    result = await hasn_node_bindings_service.create(db=db, obj=obj, user_id=user_id)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN Node Owner Binding 租约详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_hasn_node_bindings(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node Owner Binding 租约 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_node_bindings = await hasn_node_bindings_service.get(db=db, pk=pk)
    if hasn_node_bindings.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该HASN Node Owner Binding 租约')
    return response_base.success(data=hasn_node_bindings)


@router.put(
    '/{pk}',
    summary='更新HASN Node Owner Binding 租约',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_hasn_node_bindings(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node Owner Binding 租约 ID')],
    obj: UpdateHasnNodeBindingsParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_node_bindings = await hasn_node_bindings_service.get(db=db, pk=pk)
    if hasn_node_bindings.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN Node Owner Binding 租约')
    count = await hasn_node_bindings_service.update(db=db, pk=pk, obj=obj, user_id=user_id)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN Node Owner Binding 租约',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_hasn_node_bindings(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN Node Owner Binding 租约 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_node_bindings = await hasn_node_bindings_service.get(db=db, pk=pk)
    if hasn_node_bindings.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN Node Owner Binding 租约')
    from backend.app.hasn.schema.hasn_node_bindings import DeleteHasnNodeBindingsParam
    count = await hasn_node_bindings_service.delete(db=db, obj=DeleteHasnNodeBindingsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
