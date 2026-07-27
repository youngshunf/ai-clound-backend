"""热榜快照（全局，去重，喂选题；可选数据源） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.hot_topic import (
    CreateHotTopicParam,
    UpdateHotTopicParam,
)
from backend.app.hasn_creator.service.hot_topic_service import hot_topic_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='热榜快照（全局，去重，喂选题；可选数据源）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_hot_topic',
)
async def agent_list_hot_topic(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await hot_topic_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_hot_topic',
)
async def agent_create_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHotTopicParam,
) -> ResponseModel:
    await hot_topic_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取热榜快照（全局，去重，喂选题；可选数据源）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_hot_topic',
)
async def agent_get_hot_topic(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
) -> ResponseModel:
    hot_topic = await hot_topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hot_topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该热榜快照（全局，去重，喂选题；可选数据源）')
    return response_base.success(data=hot_topic)


@router.put(
    '/{pk}',
    summary='更新热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_hot_topic',
)
async def agent_update_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
    obj: UpdateHotTopicParam,
) -> ResponseModel:
    await hot_topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hot_topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该热榜快照（全局，去重，喂选题；可选数据源）')
    count = await hot_topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除热榜快照（全局，去重，喂选题；可选数据源）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_hot_topic',
)
async def agent_delete_hot_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='热榜快照（全局，去重，喂选题；可选数据源） ID')],
) -> ResponseModel:
    await hot_topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hot_topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该热榜快照（全局，去重，喂选题；可选数据源）')
    from backend.app.hasn_creator.schema.hot_topic import DeleteHotTopicParam
    count = await hot_topic_service.delete(db=db, obj=DeleteHotTopicParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
