from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hermes.schema.hermes_agent_channel_binding import (
    CreateHermesAgentChannelBindingParam,
    DeleteHermesAgentChannelBindingParam,
    GetHermesAgentChannelBindingDetail,
    UpdateHermesAgentChannelBindingParam,
)
from backend.app.hermes.service.hermes_agent_channel_binding_service import hermes_agent_channel_binding_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Hermes Agent 渠道绑定详情', dependencies=[DependsJwtAuth], name='admin_get_hermes_agent_channel_binding')
async def get_hermes_agent_channel_binding(
    db: CurrentSession, pk: Annotated[int, Path(description='Hermes Agent 渠道绑定 ID')]
) -> ResponseSchemaModel[GetHermesAgentChannelBindingDetail]:
    hermes_agent_channel_binding = await hermes_agent_channel_binding_service.get(db=db, pk=pk)
    return response_base.success(data=hermes_agent_channel_binding)


@router.get(
    '',
    summary='分页获取所有Hermes Agent 渠道绑定',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hermes_agent_channel_bindings_paginated')
async def get_hermes_agent_channel_bindings_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHermesAgentChannelBindingDetail]]:
    page_data = await hermes_agent_channel_binding_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Hermes Agent 渠道绑定',
    dependencies=[
        Depends(RequestPermission('hermes:agent:channel:binding:add')),
        DependsRBAC,
    ],
)
async def create_hermes_agent_channel_binding(db: CurrentSessionTransaction, obj: CreateHermesAgentChannelBindingParam) -> ResponseModel:
    await hermes_agent_channel_binding_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Hermes Agent 渠道绑定',
    dependencies=[
        Depends(RequestPermission('hermes:agent:channel:binding:edit')),
        DependsRBAC,
    ],
)
async def update_hermes_agent_channel_binding(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Hermes Agent 渠道绑定 ID')], obj: UpdateHermesAgentChannelBindingParam
) -> ResponseModel:
    count = await hermes_agent_channel_binding_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Hermes Agent 渠道绑定',
    dependencies=[
        Depends(RequestPermission('hermes:agent:channel:binding:del')),
        DependsRBAC,
    ],
)
async def delete_hermes_agent_channel_bindings(db: CurrentSessionTransaction, obj: DeleteHermesAgentChannelBindingParam) -> ResponseModel:
    count = await hermes_agent_channel_binding_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
