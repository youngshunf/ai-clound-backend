"""短视频项目（reel：一组创作的容器 + 默认创作参数） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_reel.schema.reel_project import (
    CreateReelProjectParam,
    UpdateReelProjectParam,
)
from backend.app.hasn_reel.service.reel_project_service import reel_project_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='短视频项目（reel：一组创作的容器 + 默认创作参数）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_list_reel_project',
)
async def agent_list_reel_project(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await reel_project_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_create_reel_project',
)
async def agent_create_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateReelProjectParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await reel_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取短视频项目（reel：一组创作的容器 + 默认创作参数）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_get_reel_project',
)
async def agent_get_reel_project(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    reel_project = await reel_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    return response_base.success(data=reel_project)


@router.put(
    '/{pk}',
    summary='更新短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_update_reel_project',
)
async def agent_update_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
    obj: UpdateReelProjectParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    reel_project = await reel_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    count = await reel_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_delete_reel_project',
)
async def agent_delete_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    reel_project = await reel_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    from backend.app.hasn_reel.schema.reel_project import DeleteReelProjectParam
    count = await reel_project_service.delete(db=db, obj=DeleteReelProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
