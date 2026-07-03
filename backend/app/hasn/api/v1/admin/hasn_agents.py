from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_agents import (
    CreateHasnAgentsParam,
    DeleteHasnAgentsParam,
    GetHasnAgentsDetail,
    UpdateHasnAgentsParam,
)
from backend.app.hasn.service.hasn_agents_service import hasn_agents_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Agent 详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_agents')
async def get_hasn_agents(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Agent  ID')]
) -> ResponseSchemaModel[GetHasnAgentsDetail]:
    hasn_agents = await hasn_agents_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_agents)


@router.get(
    '',
    summary='分页获取所有HASN Agent ',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_agentss_paginated')
async def get_hasn_agentss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAgentsDetail]]:
    page_data = await hasn_agents_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Agent ',
    dependencies=[
        Depends(RequestPermission('hasn:agents:add')),
        DependsRBAC,
    ],
)
async def create_hasn_agents(db: CurrentSessionTransaction, obj: CreateHasnAgentsParam) -> ResponseModel:
    await hasn_agents_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Agent ',
    dependencies=[
        Depends(RequestPermission('hasn:agents:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_agents(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Agent  ID')], obj: UpdateHasnAgentsParam
) -> ResponseModel:
    count = await hasn_agents_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Agent ',
    dependencies=[
        Depends(RequestPermission('hasn:agents:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_agentss(db: CurrentSessionTransaction, obj: DeleteHasnAgentsParam) -> ResponseModel:
    count = await hasn_agents_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
