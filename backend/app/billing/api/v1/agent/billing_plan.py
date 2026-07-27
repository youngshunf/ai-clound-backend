"""商品档位（价格+配额快照+试用/宽限策略） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.billing.schema.billing_plan import (
    CreateBillingPlanParam,
    UpdateBillingPlanParam,
)
from backend.app.billing.service.billing_plan_service import billing_plan_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='商品档位（价格+配额快照+试用/宽限策略）列表',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_list_billing_plan',
)
async def agent_list_billing_plan(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await billing_plan_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_create_billing_plan',
)
async def agent_create_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateBillingPlanParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    await billing_plan_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取商品档位（价格+配额快照+试用/宽限策略）详情',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_get_billing_plan',
)
async def agent_get_billing_plan(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_plan.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该商品档位（价格+配额快照+试用/宽限策略）')
    return response_base.success(data=billing_plan)


@router.put(
    '/{pk}',
    summary='更新商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_update_billing_plan',
)
async def agent_update_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
    obj: UpdateBillingPlanParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_plan.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该商品档位（价格+配额快照+试用/宽限策略）')
    count = await billing_plan_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_delete_billing_plan',
)
async def agent_delete_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_plan.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该商品档位（价格+配额快照+试用/宽限策略）')
    from backend.app.billing.schema.billing_plan import DeleteBillingPlanParam
    count = await billing_plan_service.delete(db=db, obj=DeleteBillingPlanParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
