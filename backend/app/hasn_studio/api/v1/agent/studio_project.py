"""视频项目（统一视频引擎 studio：管线/素材/成品的容器） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_project import (
    CreateStudioProjectParam,
    UpdateStudioProjectParam,
)
from backend.app.hasn_studio.service.studio_project_service import studio_project_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='视频项目（统一视频引擎 studio：管线/素材/成品的容器）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_list_studio_project',
)
async def agent_list_studio_project(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await studio_project_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_create_studio_project',
)
async def agent_create_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioProjectParam,
) -> ResponseModel:
    result = await studio_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_get_studio_project',
)
async def agent_get_studio_project(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
) -> ResponseModel:
    studio_project = await studio_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    return response_base.success(data=studio_project)


@router.put(
    '/{pk}',
    summary='更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_update_studio_project',
)
async def agent_update_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
    obj: UpdateStudioProjectParam,
) -> ResponseModel:
    await studio_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    count = await studio_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_delete_studio_project',
)
async def agent_delete_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
) -> ResponseModel:
    await studio_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    from backend.app.hasn_studio.schema.studio_project import DeleteStudioProjectParam
    count = await studio_project_service.delete(db=db, obj=DeleteStudioProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
