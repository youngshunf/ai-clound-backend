"""获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.playbook import (
    CreatePlaybookParam,
    UpdatePlaybookParam,
)
from backend.app.hasn_creator.service.playbook_service import playbook_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_playbook',
)
async def agent_list_playbook(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await playbook_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_playbook',
)
async def agent_create_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreatePlaybookParam,
) -> ResponseModel:
    result = await playbook_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_playbook',
)
async def agent_get_playbook(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
) -> ResponseModel:
    playbook = await playbook_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if playbook.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    return response_base.success(data=playbook)


@router.put(
    '/{pk}',
    summary='更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_playbook',
)
async def agent_update_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
    obj: UpdatePlaybookParam,
) -> ResponseModel:
    await playbook_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if playbook.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    count = await playbook_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_playbook',
)
async def agent_delete_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
) -> ResponseModel:
    await playbook_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if playbook.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    from backend.app.hasn_creator.schema.playbook import DeletePlaybookParam
    count = await playbook_service.delete(db=db, obj=DeletePlaybookParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
