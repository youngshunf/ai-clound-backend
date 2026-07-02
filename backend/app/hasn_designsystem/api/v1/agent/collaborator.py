"""设计系统协作分身绑定（对齐 DECKBIND） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.collaborator import (
    CreateCollaboratorParam,
    UpdateCollaboratorParam,
)
from backend.app.hasn_designsystem.service.collaborator_service import collaborator_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='设计系统协作分身绑定（对齐 DECKBIND）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_list_collaborator',
)
async def agent_list_collaborator(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await collaborator_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_create_collaborator',
)
async def agent_create_collaborator(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateCollaboratorParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await collaborator_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计系统协作分身绑定（对齐 DECKBIND）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_get_collaborator',
)
async def agent_get_collaborator(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    collaborator = await collaborator_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if collaborator.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该设计系统协作分身绑定（对齐 DECKBIND）')
    return response_base.success(data=collaborator)


@router.put(
    '/{pk}',
    summary='更新设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_update_collaborator',
)
async def agent_update_collaborator(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')],
    obj: UpdateCollaboratorParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    collaborator = await collaborator_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if collaborator.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该设计系统协作分身绑定（对齐 DECKBIND）')
    count = await collaborator_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_delete_collaborator',
)
async def agent_delete_collaborator(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    collaborator = await collaborator_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if collaborator.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该设计系统协作分身绑定（对齐 DECKBIND）')
    from backend.app.hasn_designsystem.schema.collaborator import DeleteCollaboratorParam
    count = await collaborator_service.delete(db=db, obj=DeleteCollaboratorParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
