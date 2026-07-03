from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_clients import (
    CreateHasnClientsParam,
    DeleteHasnClientsParam,
    GetHasnClientsDetail,
    UpdateHasnClientsParam,
)
from backend.app.hasn.service.hasn_clients_service import hasn_clients_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 客户端设备详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_clients')
async def get_hasn_clients(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 客户端设备 ID')]
) -> ResponseSchemaModel[GetHasnClientsDetail]:
    hasn_clients = await hasn_clients_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_clients)


@router.get(
    '',
    summary='分页获取所有HASN 客户端设备',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_clientss_paginated')
async def get_hasn_clientss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnClientsDetail]]:
    page_data = await hasn_clients_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 客户端设备',
    dependencies=[
        Depends(RequestPermission('hasn:clients:add')),
        DependsRBAC,
    ],
)
async def create_hasn_clients(db: CurrentSessionTransaction, obj: CreateHasnClientsParam) -> ResponseModel:
    await hasn_clients_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 客户端设备',
    dependencies=[
        Depends(RequestPermission('hasn:clients:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_clients(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 客户端设备 ID')], obj: UpdateHasnClientsParam
) -> ResponseModel:
    count = await hasn_clients_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 客户端设备',
    dependencies=[
        Depends(RequestPermission('hasn:clients:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_clientss(db: CurrentSessionTransaction, obj: DeleteHasnClientsParam) -> ResponseModel:
    count = await hasn_clients_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
