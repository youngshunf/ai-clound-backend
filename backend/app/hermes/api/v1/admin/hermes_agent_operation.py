from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hermes.schema.hermes_agent_operation import (
    CreateHermesAgentOperationParam,
    DeleteHermesAgentOperationParam,
    GetHermesAgentOperationDetail,
    UpdateHermesAgentOperationParam,
)
from backend.app.hermes.service.hermes_agent_operation_service import hermes_agent_operation_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Hermes Agent 操作记录详情', dependencies=[DependsJwtAuth], name='admin_get_hermes_agent_operation')
async def get_hermes_agent_operation(
    db: CurrentSession, pk: Annotated[int, Path(description='Hermes Agent 操作记录 ID')]
) -> ResponseSchemaModel[GetHermesAgentOperationDetail]:
    hermes_agent_operation = await hermes_agent_operation_service.get(db=db, pk=pk)
    return response_base.success(data=hermes_agent_operation)


@router.get(
    '',
    summary='分页获取所有Hermes Agent 操作记录',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hermes_agent_operations_paginated')
async def get_hermes_agent_operations_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHermesAgentOperationDetail]]:
    page_data = await hermes_agent_operation_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Hermes Agent 操作记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:operation:add')),
        DependsRBAC,
    ],
)
async def create_hermes_agent_operation(db: CurrentSessionTransaction, obj: CreateHermesAgentOperationParam) -> ResponseModel:
    await hermes_agent_operation_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Hermes Agent 操作记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:operation:edit')),
        DependsRBAC,
    ],
)
async def update_hermes_agent_operation(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Hermes Agent 操作记录 ID')], obj: UpdateHermesAgentOperationParam
) -> ResponseModel:
    count = await hermes_agent_operation_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Hermes Agent 操作记录',
    dependencies=[
        Depends(RequestPermission('hermes:agent:operation:del')),
        DependsRBAC,
    ],
)
async def delete_hermes_agent_operations(db: CurrentSessionTransaction, obj: DeleteHermesAgentOperationParam) -> ResponseModel:
    count = await hermes_agent_operation_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
