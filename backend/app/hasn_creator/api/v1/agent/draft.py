"""草稿箱（灵感快速捕获，轻量独立于正式流水线） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.draft import (
    CreateDraftParam,
    UpdateDraftParam,
)
from backend.app.hasn_creator.service.draft_service import draft_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='草稿箱（灵感快速捕获，轻量独立于正式流水线）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_draft',
)
async def agent_list_draft(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await draft_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_draft',
)
async def agent_create_draft(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateDraftParam,
) -> ResponseModel:
    await draft_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取草稿箱（灵感快速捕获，轻量独立于正式流水线）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_draft',
)
async def agent_get_draft(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
) -> ResponseModel:
    draft = await draft_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if draft.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    return response_base.success(data=draft)


@router.put(
    '/{pk}',
    summary='更新草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_draft',
)
async def agent_update_draft(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
    obj: UpdateDraftParam,
) -> ResponseModel:
    await draft_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if draft.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    count = await draft_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_draft',
)
async def agent_delete_draft(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
) -> ResponseModel:
    await draft_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if draft.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    from backend.app.hasn_creator.schema.draft import DeleteDraftParam
    count = await draft_service.delete(db=db, obj=DeleteDraftParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
