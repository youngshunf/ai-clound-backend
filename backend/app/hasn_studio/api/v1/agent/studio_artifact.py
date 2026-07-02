"""视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_artifact import (
    CreateStudioArtifactParam,
    UpdateStudioArtifactParam,
)
from backend.app.hasn_studio.service.studio_artifact_service import studio_artifact_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_list_studio_artifact',
)
async def agent_list_studio_artifact(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await studio_artifact_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_create_studio_artifact',
)
async def agent_create_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioArtifactParam,
) -> ResponseModel:
    result = await studio_artifact_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_get_studio_artifact',
)
async def agent_get_studio_artifact(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
) -> ResponseModel:
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_artifact.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    return response_base.success(data=studio_artifact)


@router.put(
    '/{pk}',
    summary='更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_update_studio_artifact',
)
async def agent_update_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
    obj: UpdateStudioArtifactParam,
) -> ResponseModel:
    await studio_artifact_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_artifact.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    count = await studio_artifact_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_delete_studio_artifact',
)
async def agent_delete_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
) -> ResponseModel:
    await studio_artifact_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_artifact.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    from backend.app.hasn_studio.schema.studio_artifact import DeleteStudioArtifactParam
    count = await studio_artifact_service.delete(db=db, obj=DeleteStudioArtifactParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
