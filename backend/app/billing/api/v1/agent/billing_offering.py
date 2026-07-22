"""商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.billing.schema.billing_offering import (
    CreateBillingOfferingParam,
    UpdateBillingOfferingParam,
)
from backend.app.billing.service.billing_offering_service import billing_offering_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）列表',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_list_billing_offering',
)
async def agent_list_billing_offering(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await billing_offering_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_create_billing_offering',
)
async def agent_create_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateBillingOfferingParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    await billing_offering_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）详情',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_get_billing_offering',
)
async def agent_get_billing_offering(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_offering.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    return response_base.success(data=billing_offering)


@router.put(
    '/{pk}',
    summary='更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_update_billing_offering',
)
async def agent_update_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
    obj: UpdateBillingOfferingParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_offering.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    count = await billing_offering_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsAgentJwtAuth],
    name='billing_agent_delete_billing_offering',
)
async def agent_delete_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if billing_offering.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    from backend.app.billing.schema.billing_offering import DeleteBillingOfferingParam
    count = await billing_offering_service.delete(db=db, obj=DeleteBillingOfferingParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
