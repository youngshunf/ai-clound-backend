"""模板版本 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    UpdateMarketplaceTemplateVersionParam,
)
from backend.app.marketplace.service.marketplace_template_version_service import marketplace_template_version_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='模板版本列表',
    dependencies=[DependsAgentJwtAuth],
    name='agent_list_marketplace_template_version',
)
async def agent_list_marketplace_template_version(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await marketplace_template_version_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建模板版本',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_marketplace_template_version',
)
async def agent_create_marketplace_template_version(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateMarketplaceTemplateVersionParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    result = await marketplace_template_version_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取模板版本详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_marketplace_template_version',
)
async def agent_get_marketplace_template_version(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='模板版本 ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    marketplace_template_version = await marketplace_template_version_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template_version.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该模板版本')
    return response_base.success(data=marketplace_template_version)


@router.put(
    '/{pk}',
    summary='更新模板版本',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_marketplace_template_version',
)
async def agent_update_marketplace_template_version(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='模板版本 ID')],
    obj: UpdateMarketplaceTemplateVersionParam,
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    marketplace_template_version = await marketplace_template_version_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template_version.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该模板版本')
    count = await marketplace_template_version_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除模板版本',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_marketplace_template_version',
)
async def agent_delete_marketplace_template_version(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='模板版本 ID')],
) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent
    marketplace_template_version = await marketplace_template_version_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template_version.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该模板版本')
    from backend.app.marketplace.schema.marketplace_template_version import DeleteMarketplaceTemplateVersionParam
    count = await marketplace_template_version_service.delete(db=db, obj=DeleteMarketplaceTemplateVersionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
