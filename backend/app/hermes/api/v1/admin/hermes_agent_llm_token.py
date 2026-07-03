from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hermes.schema.hermes_agent_llm_token import (
    CreateHermesAgentLlmTokenParam,
    DeleteHermesAgentLlmTokenParam,
    GetHermesAgentLlmTokenDetail,
    UpdateHermesAgentLlmTokenParam,
)
from backend.app.hermes.service.hermes_agent_llm_token_service import hermes_agent_llm_token_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Hermes Agent 级 LLM token 隔离记录详情', dependencies=[DependsJwtAuth], name='admin_get_hermes_agent_llm_token')
async def get_hermes_agent_llm_token(
    db: CurrentSession, pk: Annotated[int, Path(description='Hermes Agent 级 LLM token 隔离记录 ID')]
) -> ResponseSchemaModel[GetHermesAgentLlmTokenDetail]:
    hermes_agent_llm_token = await hermes_agent_llm_token_service.get(db=db, pk=pk)
    return response_base.success(data=hermes_agent_llm_token)


@router.get(
    '',
    summary='分页获取所有Hermes Agent 级 LLM token 隔离记录',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hermes_agent_llm_tokens_paginated')
async def get_hermes_agent_llm_tokens_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHermesAgentLlmTokenDetail]]:
    page_data = await hermes_agent_llm_token_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Hermes Agent 级 LLM token 隔离记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:llm:token:add')),
        DependsRBAC,
    ],
)
async def create_hermes_agent_llm_token(db: CurrentSessionTransaction, obj: CreateHermesAgentLlmTokenParam) -> ResponseModel:
    await hermes_agent_llm_token_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Hermes Agent 级 LLM token 隔离记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:llm:token:edit')),
        DependsRBAC,
    ],
)
async def update_hermes_agent_llm_token(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Hermes Agent 级 LLM token 隔离记录 ID')], obj: UpdateHermesAgentLlmTokenParam
) -> ResponseModel:
    count = await hermes_agent_llm_token_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Hermes Agent 级 LLM token 隔离记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:llm:token:del')),
        DependsRBAC,
    ],
)
async def delete_hermes_agent_llm_tokens(db: CurrentSessionTransaction, obj: DeleteHermesAgentLlmTokenParam) -> ResponseModel:
    count = await hermes_agent_llm_token_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
