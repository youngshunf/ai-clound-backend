"""技能市场模板表（Agent模板/技能包/SOP包） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='技能市场模板表（Agent模板/技能包/SOP包）列表',
    dependencies=[DependsAgentJwtAuth],
    name='agent_list_marketplace_template',
)
async def agent_list_marketplace_template(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await marketplace_template_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_marketplace_template',
)
async def agent_create_marketplace_template(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateMarketplaceTemplateParam,
) -> ResponseModel:
    result = await marketplace_template_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取技能市场模板表（Agent模板/技能包/SOP包）详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_marketplace_template',
)
async def agent_get_marketplace_template(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='技能市场模板表（Agent模板/技能包/SOP包） ID')],
) -> ResponseModel:
    marketplace_template = await marketplace_template_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该技能市场模板表（Agent模板/技能包/SOP包）')
    return response_base.success(data=marketplace_template)


@router.put(
    '/{pk}',
    summary='更新技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_marketplace_template',
)
async def agent_update_marketplace_template(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='技能市场模板表（Agent模板/技能包/SOP包） ID')],
    obj: UpdateMarketplaceTemplateParam,
) -> ResponseModel:
    await marketplace_template_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该技能市场模板表（Agent模板/技能包/SOP包）')
    count = await marketplace_template_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_marketplace_template',
)
async def agent_delete_marketplace_template(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='技能市场模板表（Agent模板/技能包/SOP包） ID')],
) -> ResponseModel:
    await marketplace_template_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_template.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该技能市场模板表（Agent模板/技能包/SOP包）')
    from backend.app.marketplace.schema.marketplace_template import DeleteMarketplaceTemplateParam
    count = await marketplace_template_service.delete(db=db, obj=DeleteMarketplaceTemplateParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
