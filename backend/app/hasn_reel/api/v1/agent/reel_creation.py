"""一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_reel.schema.reel_creation import (
    CreateReelCreationParam,
    UpdateReelCreationParam,
)
from backend.app.hasn_reel.service.reel_creation_service import reel_creation_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_list_reel_creation',
)
async def agent_list_reel_creation(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await reel_creation_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_create_reel_creation',
)
async def agent_create_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateReelCreationParam,
) -> ResponseModel:
    result = await reel_creation_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_get_reel_creation',
)
async def agent_get_reel_creation(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
) -> ResponseModel:
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_creation.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    return response_base.success(data=reel_creation)


@router.put(
    '/{pk}',
    summary='更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_update_reel_creation',
)
async def agent_update_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
    obj: UpdateReelCreationParam,
) -> ResponseModel:
    await reel_creation_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_creation.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    count = await reel_creation_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_reel_agent_delete_reel_creation',
)
async def agent_delete_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
) -> ResponseModel:
    await reel_creation_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if reel_creation.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    from backend.app.hasn_reel.schema.reel_creation import DeleteReelCreationParam
    count = await reel_creation_service.delete(db=db, obj=DeleteReelCreationParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
