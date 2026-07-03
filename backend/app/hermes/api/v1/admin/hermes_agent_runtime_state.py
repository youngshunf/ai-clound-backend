from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hermes.schema.hermes_agent_runtime_state import (
    CreateHermesAgentRuntimeStateParam,
    DeleteHermesAgentRuntimeStateParam,
    GetHermesAgentRuntimeStateDetail,
    UpdateHermesAgentRuntimeStateParam,
)
from backend.app.hermes.service.hermes_agent_runtime_state_service import hermes_agent_runtime_state_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Hermes Agent Runtime 状态详情', dependencies=[DependsJwtAuth], name='admin_get_hermes_agent_runtime_state')
async def get_hermes_agent_runtime_state(
    db: CurrentSession, pk: Annotated[int, Path(description='Hermes Agent Runtime 状态 ID')]
) -> ResponseSchemaModel[GetHermesAgentRuntimeStateDetail]:
    hermes_agent_runtime_state = await hermes_agent_runtime_state_service.get(db=db, pk=pk)
    return response_base.success(data=hermes_agent_runtime_state)


@router.get(
    '',
    summary='分页获取所有Hermes Agent Runtime 状态',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hermes_agent_runtime_states_paginated')
async def get_hermes_agent_runtime_states_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHermesAgentRuntimeStateDetail]]:
    page_data = await hermes_agent_runtime_state_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Hermes Agent Runtime 状态',
    dependencies=[
        Depends(RequestPermission('hermes:agent:runtime:state:add')),
        DependsRBAC,
    ],
)
async def create_hermes_agent_runtime_state(db: CurrentSessionTransaction, obj: CreateHermesAgentRuntimeStateParam) -> ResponseModel:
    await hermes_agent_runtime_state_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Hermes Agent Runtime 状态',
    dependencies=[
        Depends(RequestPermission('hermes:agent:runtime:state:edit')),
        DependsRBAC,
    ],
)
async def update_hermes_agent_runtime_state(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Hermes Agent Runtime 状态 ID')], obj: UpdateHermesAgentRuntimeStateParam
) -> ResponseModel:
    count = await hermes_agent_runtime_state_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Hermes Agent Runtime 状态',
    dependencies=[
        Depends(RequestPermission('hermes:agent:runtime:state:del')),
        DependsRBAC,
    ],
)
async def delete_hermes_agent_runtime_states(db: CurrentSessionTransaction, obj: DeleteHermesAgentRuntimeStateParam) -> ResponseModel:
    count = await hermes_agent_runtime_state_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
