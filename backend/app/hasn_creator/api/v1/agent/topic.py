"""选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.topic import (
    CreateTopicParam,
    UpdateTopicParam,
)
from backend.app.hasn_creator.service.topic_service import topic_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_topic',
)
async def agent_list_topic(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await topic_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_topic',
)
async def agent_create_topic(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateTopicParam,
) -> ResponseModel:
    result = await topic_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_topic',
)
async def agent_get_topic(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
) -> ResponseModel:
    topic = await topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    return response_base.success(data=topic)


@router.put(
    '/{pk}',
    summary='更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_topic',
)
async def agent_update_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
    obj: UpdateTopicParam,
) -> ResponseModel:
    await topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    count = await topic_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_topic',
)
async def agent_delete_topic(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID')],
) -> ResponseModel:
    await topic_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if topic.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过')
    from backend.app.hasn_creator.schema.topic import DeleteTopicParam
    count = await topic_service.delete(db=db, obj=DeleteTopicParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
