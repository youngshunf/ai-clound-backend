"""设计系统（云端权威） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.design_system import (
    CreateDesignSystemParam,
    UpdateDesignSystemParam,
)
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='设计系统（云端权威）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_list_design_system',
)
async def agent_list_design_system(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await design_system_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建设计系统（云端权威）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_create_design_system',
)
async def agent_create_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateDesignSystemParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await design_system_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计系统（云端权威）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_get_design_system',
)
async def agent_get_design_system(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    design_system = await design_system_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if design_system.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该设计系统（云端权威）')
    return response_base.success(data=design_system)


@router.put(
    '/{pk}',
    summary='更新设计系统（云端权威）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_update_design_system',
)
async def agent_update_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
    obj: UpdateDesignSystemParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    design_system = await design_system_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if design_system.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该设计系统（云端权威）')
    count = await design_system_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计系统（云端权威）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_delete_design_system',
)
async def agent_delete_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    design_system = await design_system_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if design_system.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该设计系统（云端权威）')
    from backend.app.hasn_designsystem.schema.design_system import DeleteDesignSystemParam
    count = await design_system_service.delete(db=db, obj=DeleteDesignSystemParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
