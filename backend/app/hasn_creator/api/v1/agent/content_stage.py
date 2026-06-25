"""阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content_stage import (
    CreateContentStageParam,
    UpdateContentStageParam,
)
from backend.app.hasn_creator.service.content_stage_service import content_stage_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_content_stage',
)
async def agent_list_content_stage(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await content_stage_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_content_stage',
)
async def agent_create_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentStageParam,
) -> ResponseModel:
    result = await content_stage_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_content_stage',
)
async def agent_get_content_stage(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
) -> ResponseModel:
    content_stage = await content_stage_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_stage.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    return response_base.success(data=content_stage)


@router.put(
    '/{pk}',
    summary='更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_content_stage',
)
async def agent_update_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
    obj: UpdateContentStageParam,
) -> ResponseModel:
    await content_stage_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_stage.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    count = await content_stage_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_content_stage',
)
async def agent_delete_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
) -> ResponseModel:
    await content_stage_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_stage.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    from backend.app.hasn_creator.schema.content_stage import DeleteContentStageParam
    count = await content_stage_service.delete(db=db, obj=DeleteContentStageParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
