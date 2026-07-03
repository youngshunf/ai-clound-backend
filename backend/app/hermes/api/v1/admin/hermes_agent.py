from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hermes.schema.hermes_agent import (
    CreateHermesAgentParam,
    DeleteHermesAgentParam,
    GetHermesAgentDetail,
    UpdateHermesAgentParam,
)
from backend.app.hermes.service.hermes_agent_service import hermes_agent_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Hermes Agent 详情', dependencies=[DependsJwtAuth], name='admin_get_hermes_agent')
async def get_hermes_agent(
    db: CurrentSession, pk: Annotated[int, Path(description='Hermes Agent  ID')]
) -> ResponseSchemaModel[GetHermesAgentDetail]:
    hermes_agent = await hermes_agent_service.get(db=db, pk=pk)
    return response_base.success(data=hermes_agent)


@router.get(
    '',
    summary='分页获取所有Hermes Agent ',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hermes_agents_paginated')
async def get_hermes_agents_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHermesAgentDetail]]:
    page_data = await hermes_agent_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Hermes Agent ',
    dependencies=[
        Depends(RequestPermission('hermes:agent:add')),
        DependsRBAC,
    ],
)
async def create_hermes_agent(db: CurrentSessionTransaction, obj: CreateHermesAgentParam) -> ResponseModel:
    await hermes_agent_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Hermes Agent ',
    dependencies=[
        Depends(RequestPermission('hermes:agent:edit')),
        DependsRBAC,
    ],
)
async def update_hermes_agent(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Hermes Agent  ID')], obj: UpdateHermesAgentParam
) -> ResponseModel:
    count = await hermes_agent_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Hermes Agent ',
    dependencies=[
        Depends(RequestPermission('hermes:agent:del')),
        DependsRBAC,
    ],
)
async def delete_hermes_agents(db: CurrentSessionTransaction, obj: DeleteHermesAgentParam) -> ResponseModel:
    count = await hermes_agent_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
