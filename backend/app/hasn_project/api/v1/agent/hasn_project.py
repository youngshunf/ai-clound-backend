"""平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_project.schema.hasn_project import (
    CreateHasnProjectParam,
    UpdateHasnProjectParam,
)
from backend.app.hasn_project.service.hasn_project_service import hasn_project_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_project_agent_list_hasn_project',
)
async def agent_list_hasn_project(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await hasn_project_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_project_agent_create_hasn_project',
)
async def agent_create_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnProjectParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await hasn_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_project_agent_get_hasn_project',
)
async def agent_get_hasn_project(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    return response_base.success(data=hasn_project)


@router.put(
    '/{pk}',
    summary='更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_project_agent_update_hasn_project',
)
async def agent_update_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
    obj: UpdateHasnProjectParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    count = await hasn_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_project_agent_delete_hasn_project',
)
async def agent_delete_hasn_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_project = await hasn_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）')
    from backend.app.hasn_project.schema.hasn_project import DeleteHasnProjectParam
    count = await hasn_project_service.delete(db=db, obj=DeleteHasnProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
