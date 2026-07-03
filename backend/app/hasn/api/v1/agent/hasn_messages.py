"""HASN 消息 - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_messages import (
    CreateHasnMessagesParam,
    UpdateHasnMessagesParam,
)
from backend.app.hasn.service.hasn_messages_service import hasn_messages_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='HASN 消息列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_hasn_messagess(
    db: CurrentSession,
    request: Request) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    data = await hasn_messages_service.get_list(db=db, user_id=user_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建HASN 消息',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_hasn_messages(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateHasnMessagesParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    result = await hasn_messages_service.create(db=db, obj=obj, user_id=user_id)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 消息详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_hasn_messages(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN 消息 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_messages = await hasn_messages_service.get(db=db, pk=pk)
    if hasn_messages.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该HASN 消息')
    return response_base.success(data=hasn_messages)


@router.put(
    '/{pk}',
    summary='更新HASN 消息',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_hasn_messages(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN 消息 ID')],
    obj: UpdateHasnMessagesParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_messages = await hasn_messages_service.get(db=db, pk=pk)
    if hasn_messages.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN 消息')
    count = await hasn_messages_service.update(db=db, pk=pk, obj=obj, user_id=user_id)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 消息',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_hasn_messages(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='HASN 消息 ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    hasn_messages = await hasn_messages_service.get(db=db, pk=pk)
    if hasn_messages.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN 消息')
    from backend.app.hasn.schema.hasn_messages import DeleteHasnMessagesParam
    count = await hasn_messages_service.delete(db=db, obj=DeleteHasnMessagesParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
