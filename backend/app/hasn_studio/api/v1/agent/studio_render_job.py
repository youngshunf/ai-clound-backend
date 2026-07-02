"""视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_render_job import (
    CreateStudioRenderJobParam,
    UpdateStudioRenderJobParam,
)
from backend.app.hasn_studio.service.studio_render_job_service import studio_render_job_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_list_studio_render_job',
)
async def agent_list_studio_render_job(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await studio_render_job_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_create_studio_render_job',
)
async def agent_create_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioRenderJobParam,
) -> ResponseModel:
    result = await studio_render_job_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_get_studio_render_job',
)
async def agent_get_studio_render_job(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
) -> ResponseModel:
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_render_job.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    return response_base.success(data=studio_render_job)


@router.put(
    '/{pk}',
    summary='更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_update_studio_render_job',
)
async def agent_update_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
    obj: UpdateStudioRenderJobParam,
) -> ResponseModel:
    await studio_render_job_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_render_job.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    count = await studio_render_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_delete_studio_render_job',
)
async def agent_delete_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
) -> ResponseModel:
    await studio_render_job_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_render_job.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    from backend.app.hasn_studio.schema.studio_render_job import DeleteStudioRenderJobParam
    count = await studio_render_job_service.delete(db=db, obj=DeleteStudioRenderJobParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
