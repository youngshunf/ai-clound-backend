from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_agent_capabilities import (
    CreateHasnAgentCapabilitiesParam,
    DeleteHasnAgentCapabilitiesParam,
    GetHasnAgentCapabilitiesDetail,
    UpdateHasnAgentCapabilitiesParam,
)
from backend.app.hasn.service.hasn_agent_capabilities_service import hasn_agent_capabilities_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Agent 能力声明详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_agent_capabilities')
async def get_hasn_agent_capabilities(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Agent 能力声明 ID')]
) -> ResponseSchemaModel[GetHasnAgentCapabilitiesDetail]:
    hasn_agent_capabilities = await hasn_agent_capabilities_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_agent_capabilities)


@router.get(
    '',
    summary='分页获取所有HASN Agent 能力声明',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_agent_capabilitiess_paginated')
async def get_hasn_agent_capabilitiess_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAgentCapabilitiesDetail]]:
    page_data = await hasn_agent_capabilities_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Agent 能力声明',
    dependencies=[
        Depends(RequestPermission('hasn:agent:capabilities:add')),
        DependsRBAC,
    ],
)
async def create_hasn_agent_capabilities(db: CurrentSessionTransaction, obj: CreateHasnAgentCapabilitiesParam) -> ResponseModel:
    await hasn_agent_capabilities_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Agent 能力声明',
    dependencies=[
        Depends(RequestPermission('hasn:agent:capabilities:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_agent_capabilities(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Agent 能力声明 ID')], obj: UpdateHasnAgentCapabilitiesParam
) -> ResponseModel:
    count = await hasn_agent_capabilities_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Agent 能力声明',
    dependencies=[
        Depends(RequestPermission('hasn:agent:capabilities:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_agent_capabilitiess(db: CurrentSessionTransaction, obj: DeleteHasnAgentCapabilitiesParam) -> ResponseModel:
    count = await hasn_agent_capabilities_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
