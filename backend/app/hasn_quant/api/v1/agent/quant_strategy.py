"""量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_quant.schema.quant_strategy import (
    CreateQuantStrategyParam,
    UpdateQuantStrategyParam,
)
from backend.app.hasn_quant.service.quant_strategy_service import quant_strategy_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_list_quant_strategy',
)
async def agent_list_quant_strategy(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await quant_strategy_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_create_quant_strategy',
)
async def agent_create_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateQuantStrategyParam,
) -> ResponseModel:
    await quant_strategy_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_get_quant_strategy',
)
async def agent_get_quant_strategy(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
) -> ResponseModel:
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_strategy.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    return response_base.success(data=quant_strategy)


@router.put(
    '/{pk}',
    summary='更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_update_quant_strategy',
)
async def agent_update_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
    obj: UpdateQuantStrategyParam,
) -> ResponseModel:
    await quant_strategy_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_strategy.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    count = await quant_strategy_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_delete_quant_strategy',
)
async def agent_delete_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
) -> ResponseModel:
    await quant_strategy_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_strategy.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    from backend.app.hasn_quant.schema.quant_strategy import DeleteQuantStrategyParam
    count = await quant_strategy_service.delete(db=db, obj=DeleteQuantStrategyParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
