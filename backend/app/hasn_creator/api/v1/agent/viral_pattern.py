"""爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.viral_pattern import (
    CreateViralPatternParam,
    UpdateViralPatternParam,
)
from backend.app.hasn_creator.service.viral_pattern_service import viral_pattern_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_viral_pattern',
)
async def agent_list_viral_pattern(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await viral_pattern_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_viral_pattern',
)
async def agent_create_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateViralPatternParam,
) -> ResponseModel:
    result = await viral_pattern_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_viral_pattern',
)
async def agent_get_viral_pattern(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
) -> ResponseModel:
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if viral_pattern.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    return response_base.success(data=viral_pattern)


@router.put(
    '/{pk}',
    summary='更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_viral_pattern',
)
async def agent_update_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
    obj: UpdateViralPatternParam,
) -> ResponseModel:
    await viral_pattern_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if viral_pattern.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    count = await viral_pattern_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_viral_pattern',
)
async def agent_delete_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
) -> ResponseModel:
    await viral_pattern_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if viral_pattern.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    from backend.app.hasn_creator.schema.viral_pattern import DeleteViralPatternParam
    count = await viral_pattern_service.delete(db=db, obj=DeleteViralPatternParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
