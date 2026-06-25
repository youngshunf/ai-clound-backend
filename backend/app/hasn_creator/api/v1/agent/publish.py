"""发布记录（= content × account：发到某平台账号 + 数据指标） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.publish import (
    CreatePublishParam,
    UpdatePublishParam,
)
from backend.app.hasn_creator.service.publish_service import publish_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='发布记录（= content × account：发到某平台账号 + 数据指标）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_publish',
)
async def agent_list_publish(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await publish_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_publish',
)
async def agent_create_publish(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreatePublishParam,
) -> ResponseModel:
    result = await publish_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取发布记录（= content × account：发到某平台账号 + 数据指标）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_publish',
)
async def agent_get_publish(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
) -> ResponseModel:
    publish = await publish_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if publish.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该发布记录（= content × account：发到某平台账号 + 数据指标）')
    return response_base.success(data=publish)


@router.put(
    '/{pk}',
    summary='更新发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_publish',
)
async def agent_update_publish(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
    obj: UpdatePublishParam,
) -> ResponseModel:
    await publish_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if publish.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该发布记录（= content × account：发到某平台账号 + 数据指标）')
    count = await publish_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_publish',
)
async def agent_delete_publish(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
) -> ResponseModel:
    await publish_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if publish.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该发布记录（= content × account：发到某平台账号 + 数据指标）')
    from backend.app.hasn_creator.schema.publish import DeletePublishParam
    count = await publish_service.delete(db=db, obj=DeletePublishParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
