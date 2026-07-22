"""Lead CSV export batch - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_export_batch import (
    CreateLeadExportBatchParam,
    UpdateLeadExportBatchParam,
)
from backend.app.hasn_growth.service.lead_export_batch_service import lead_export_batch_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='Lead CSV export batch列表',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_list_lead_export_batchs(
    db: CurrentSession,
    request: Request) -> ResponseModel:

    data = await lead_export_batch_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建Lead CSV export batch',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_create_lead_export_batch(
    db: CurrentSessionTransaction,
    request: Request,
    obj: CreateLeadExportBatchParam) -> ResponseModel:

    await lead_export_batch_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取Lead CSV export batch详情',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_lead_export_batch(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if lead_export_batch.user_id != user_id:
        raise errors.ForbiddenError(msg='无权访问该Lead CSV export batch')
    return response_base.success(data=lead_export_batch)


@router.put(
    '/{pk}',
    summary='更新Lead CSV export batch',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_update_lead_export_batch(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')],
    obj: UpdateLeadExportBatchParam) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if lead_export_batch.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该Lead CSV export batch')
    count = await lead_export_batch_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead CSV export batch',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_delete_lead_export_batch(
    db: CurrentSession,
    request: Request,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')]) -> ResponseModel:
    agent: AgentTokenPayload = request.state.agent

    user_id = agent.owner_user_id
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if lead_export_batch.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead CSV export batch')
    from backend.app.hasn_growth.schema.lead_export_batch import DeleteLeadExportBatchParam
    count = await lead_export_batch_service.delete(db=db, obj=DeleteLeadExportBatchParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
