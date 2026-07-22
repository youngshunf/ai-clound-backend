"""演示文稿版本快照（云端权威历史） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.revision import (
    CreateRevisionParam,
    UpdateRevisionParam,
)
from backend.app.hasn_designsystem.service.revision_service import revision_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='演示文稿版本快照（云端权威历史）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_list_revision',
)
async def agent_list_revision(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await revision_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建演示文稿版本快照（云端权威历史）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_create_revision',
)
async def agent_create_revision(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateRevisionParam,
) -> ResponseModel:
    await revision_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取演示文稿版本快照（云端权威历史）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_get_revision',
)
async def agent_get_revision(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
) -> ResponseModel:
    revision = await revision_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if revision.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该演示文稿版本快照（云端权威历史）')
    return response_base.success(data=revision)


@router.put(
    '/{pk}',
    summary='更新演示文稿版本快照（云端权威历史）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_update_revision',
)
async def agent_update_revision(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
    obj: UpdateRevisionParam,
) -> ResponseModel:
    await revision_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if revision.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该演示文稿版本快照（云端权威历史）')
    count = await revision_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除演示文稿版本快照（云端权威历史）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_designsystem_agent_delete_revision',
)
async def agent_delete_revision(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
) -> ResponseModel:
    await revision_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if revision.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该演示文稿版本快照（云端权威历史）')
    from backend.app.hasn_designsystem.schema.revision import DeleteRevisionParam
    count = await revision_service.delete(db=db, obj=DeleteRevisionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
