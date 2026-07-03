"""HASN 会话事件 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_session_events import (
    CreateHasnSessionEventsParam,
    UpdateHasnSessionEventsParam,
)
from backend.app.hasn.service.hasn_session_events_service import hasn_session_events_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='HASN 会话事件列表',
    dependencies=[DependsAgentJwtAuth],
    name='agent_list_hasn_session_events',
)
async def agent_list_hasn_session_events(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await hasn_session_events_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建HASN 会话事件',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_hasn_session_events',
)
async def agent_create_hasn_session_events(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnSessionEventsParam,
) -> ResponseModel:
    result = await hasn_session_events_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 会话事件详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_hasn_session_events',
)
async def agent_get_hasn_session_events(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN 会话事件 ID')],
) -> ResponseModel:
    hasn_session_events = await hasn_session_events_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_session_events.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该HASN 会话事件')
    return response_base.success(data=hasn_session_events)


@router.put(
    '/{pk}',
    summary='更新HASN 会话事件',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_hasn_session_events',
)
async def agent_update_hasn_session_events(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 会话事件 ID')],
    obj: UpdateHasnSessionEventsParam,
) -> ResponseModel:
    await hasn_session_events_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_session_events.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该HASN 会话事件')
    count = await hasn_session_events_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 会话事件',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_hasn_session_events',
)
async def agent_delete_hasn_session_events(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 会话事件 ID')],
) -> ResponseModel:
    await hasn_session_events_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_session_events.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该HASN 会话事件')
    from backend.app.hasn.schema.hasn_session_events import DeleteHasnSessionEventsParam
    count = await hasn_session_events_service.delete(db=db, obj=DeleteHasnSessionEventsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
