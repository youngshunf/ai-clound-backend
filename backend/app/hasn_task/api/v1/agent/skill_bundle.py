"""Skill Bundle 定义表（多个 skill 的组合） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_task.schema.skill_bundle import (
    CreateHasnSkillBundleParam,
    UpdateHasnSkillBundleParam,
)
from backend.app.hasn_task.service.skill_bundle_service import hasn_skill_bundle_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.pagination import DependsPagination
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='Skill Bundle 定义表（多个 skill 的组合）列表',
    dependencies=[DependsAgentJwtAuth, DependsPagination],
    name='agent_list_hasn_skill_bundle',
)
async def agent_list_hasn_skill_bundle(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # owner 隔离：只返回本 owner 的 bundle（不暴露其它 owner 私有任务域资源）
    data = await hasn_skill_bundle_service.get_list_by_owner(db=db, owner_id=agent.owner_hasn_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_hasn_skill_bundle',
)
async def agent_create_hasn_skill_bundle(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnSkillBundleParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 不信入参身份：强制覆盖 owner_id 为令牌身份（防伪造）
    obj = obj.model_copy(update={'owner_id': agent.owner_hasn_id})
    result = await hasn_skill_bundle_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Skill Bundle 定义表（多个 skill 的组合）详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_hasn_skill_bundle',
)
async def agent_get_hasn_skill_bundle(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Skill Bundle 定义表（多个 skill 的组合） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_skill_bundle = await hasn_skill_bundle_service.get(db=db, pk=pk)
    if hasn_skill_bundle.owner_id != agent.owner_hasn_id:
        raise errors.ForbiddenError(msg='无权访问该技能包')
    return response_base.success(data=hasn_skill_bundle)


@router.put(
    '/{pk}',
    summary='更新Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_hasn_skill_bundle',
)
async def agent_update_hasn_skill_bundle(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Skill Bundle 定义表（多个 skill 的组合） ID')],
    obj: UpdateHasnSkillBundleParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_skill_bundle = await hasn_skill_bundle_service.get(db=db, pk=pk)
    if hasn_skill_bundle.owner_id != agent.owner_hasn_id:
        raise errors.ForbiddenError(msg='无权修改该技能包')
    count = await hasn_skill_bundle_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_hasn_skill_bundle',
)
async def agent_delete_hasn_skill_bundle(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Skill Bundle 定义表（多个 skill 的组合） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    hasn_skill_bundle = await hasn_skill_bundle_service.get(db=db, pk=pk)
    if hasn_skill_bundle.owner_id != agent.owner_hasn_id:
        raise errors.ForbiddenError(msg='无权删除该技能包')
    from backend.app.hasn_task.schema.skill_bundle import DeleteHasnSkillBundleParam

    count = await hasn_skill_bundle_service.delete(db=db, obj=DeleteHasnSkillBundleParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
