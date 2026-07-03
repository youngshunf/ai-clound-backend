"""技能市场同步日志 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.service.marketplace_sync_log_service import marketplace_sync_log_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='技能市场同步日志列表',
    dependencies=[DependsAgentJwtAuth],
    name='agent_list_marketplace_sync_log',
)
async def agent_list_marketplace_sync_log(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await marketplace_sync_log_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建技能市场同步日志',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_marketplace_sync_log',
)
async def agent_create_marketplace_sync_log(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateMarketplaceSyncLogParam,
) -> ResponseModel:
    result = await marketplace_sync_log_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取技能市场同步日志详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_marketplace_sync_log',
)
async def agent_get_marketplace_sync_log(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='技能市场同步日志 ID')],
) -> ResponseModel:
    marketplace_sync_log = await marketplace_sync_log_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_sync_log.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该技能市场同步日志')
    return response_base.success(data=marketplace_sync_log)


@router.put(
    '/{pk}',
    summary='更新技能市场同步日志',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_marketplace_sync_log',
)
async def agent_update_marketplace_sync_log(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='技能市场同步日志 ID')],
    obj: UpdateMarketplaceSyncLogParam,
) -> ResponseModel:
    await marketplace_sync_log_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_sync_log.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该技能市场同步日志')
    count = await marketplace_sync_log_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除技能市场同步日志',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_marketplace_sync_log',
)
async def agent_delete_marketplace_sync_log(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='技能市场同步日志 ID')],
) -> ResponseModel:
    await marketplace_sync_log_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if marketplace_sync_log.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该技能市场同步日志')
    from backend.app.marketplace.schema.marketplace_sync_log import DeleteMarketplaceSyncLogParam
    count = await marketplace_sync_log_service.delete(db=db, obj=DeleteMarketplaceSyncLogParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
