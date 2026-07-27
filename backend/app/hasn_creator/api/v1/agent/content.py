"""内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content import (
    CreateContentParam,
    UpdateContentParam,
)
from backend.app.hasn_creator.service.content_service import content_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_content',
)
async def agent_list_content(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await content_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_content',
)
async def agent_create_content(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentParam,
) -> ResponseModel:
    # 分身建内容即认领创作分身身份：身份取自 Agent JWT（request.state.agent），入参未显式带则自动填充。
    agent = getattr(request.state, 'agent', None)
    if agent is not None and not obj.created_by_agent_id:
        obj = obj.model_copy(update={'created_by_agent_id': agent.agent_hasn_id})
    await content_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_content',
)
async def agent_get_content(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
) -> ResponseModel:
    content = await content_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    return response_base.success(data=content)


@router.put(
    '/{pk}',
    summary='更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_content',
)
async def agent_update_content(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
    obj: UpdateContentParam,
) -> ResponseModel:
    await content_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    count = await content_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_content',
)
async def agent_delete_content(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
) -> ResponseModel:
    await content_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    from backend.app.hasn_creator.schema.content import DeleteContentParam
    count = await content_service.delete(db=db, obj=DeleteContentParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
