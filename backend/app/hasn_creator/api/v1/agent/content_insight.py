"""内容洞察（复盘结构化结论，进化沉淀核心） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content_insight import (
    CreateContentInsightParam,
    UpdateContentInsightParam,
)
from backend.app.hasn_creator.service.content_insight_service import content_insight_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='内容洞察（复盘结构化结论，进化沉淀核心）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_content_insight',
)
async def agent_list_content_insight(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await content_insight_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_content_insight',
)
async def agent_create_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentInsightParam,
) -> ResponseModel:
    await content_insight_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取内容洞察（复盘结构化结论，进化沉淀核心）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_content_insight',
)
async def agent_get_content_insight(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
) -> ResponseModel:
    content_insight = await content_insight_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_insight.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该内容洞察（复盘结构化结论，进化沉淀核心）')
    return response_base.success(data=content_insight)


@router.put(
    '/{pk}',
    summary='更新内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_content_insight',
)
async def agent_update_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
    obj: UpdateContentInsightParam,
) -> ResponseModel:
    await content_insight_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_insight.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该内容洞察（复盘结构化结论，进化沉淀核心）')
    count = await content_insight_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_content_insight',
)
async def agent_delete_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
) -> ResponseModel:
    await content_insight_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if content_insight.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该内容洞察（复盘结构化结论，进化沉淀核心）')
    from backend.app.hasn_creator.schema.content_insight import DeleteContentInsightParam
    count = await content_insight_service.delete(db=db, obj=DeleteContentInsightParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
