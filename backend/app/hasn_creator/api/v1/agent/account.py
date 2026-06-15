"""平台账号（1:N project）；同一项目多平台真实账号 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.account import (
    CreateAccountParam,
    UpdateAccountParam,
)
from backend.app.hasn_creator.service.account_service import account_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='平台账号（1:N project）；同一项目多平台真实账号列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_account',
)
async def agent_list_account(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await account_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_account',
)
async def agent_create_account(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateAccountParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await account_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台账号（1:N project）；同一项目多平台真实账号详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_account',
)
async def agent_get_account(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    account = await account_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if account.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该平台账号（1:N project）；同一项目多平台真实账号')
    return response_base.success(data=account)


@router.put(
    '/{pk}',
    summary='更新平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_account',
)
async def agent_update_account(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
    obj: UpdateAccountParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    account = await account_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if account.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该平台账号（1:N project）；同一项目多平台真实账号')
    count = await account_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_account',
)
async def agent_delete_account(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    account = await account_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if account.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该平台账号（1:N project）；同一项目多平台真实账号')
    from backend.app.hasn_creator.schema.account import DeleteAccountParam
    count = await account_service.delete(db=db, obj=DeleteAccountParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
