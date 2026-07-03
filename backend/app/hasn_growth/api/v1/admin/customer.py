from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.customer import (
    CreateCustomerParam,
    DeleteCustomerParam,
    GetCustomerDetail,
    UpdateCustomerParam,
)
from backend.app.hasn_growth.service.customer_service import customer_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客客户（qualified 线索 / inbound 直建）详情', dependencies=[DependsJwtAuth], name='admin_get_customer')
async def get_customer(
    db: CurrentSession, pk: Annotated[int, Path(description='获客客户（qualified 线索 / inbound 直建） ID')]
) -> ResponseSchemaModel[GetCustomerDetail]:
    customer = await customer_service.get(db=db, pk=pk)
    return response_base.success(data=customer)


@router.get(
    '',
    summary='分页获取所有获客客户（qualified 线索 / inbound 直建）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_customer_paginated',
)
async def get_customer_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetCustomerDetail]]:
    page_data = await customer_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客客户（qualified 线索 / inbound 直建）',
    dependencies=[
        Depends(RequestPermission('customer:add')),
        DependsRBAC,
    ],
    name='admin_create_customer',
)
async def create_customer(db: CurrentSessionTransaction, obj: CreateCustomerParam) -> ResponseModel:
    await customer_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客客户（qualified 线索 / inbound 直建）',
    dependencies=[
        Depends(RequestPermission('customer:edit')),
        DependsRBAC,
    ],
    name='admin_update_customer',
)
async def update_customer(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客客户（qualified 线索 / inbound 直建） ID')], obj: UpdateCustomerParam
) -> ResponseModel:
    count = await customer_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客客户（qualified 线索 / inbound 直建）',
    dependencies=[
        Depends(RequestPermission('customer:del')),
        DependsRBAC,
    ],
    name='admin_delete_customer',
)
async def delete_customer(db: CurrentSessionTransaction, obj: DeleteCustomerParam) -> ResponseModel:
    count = await customer_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
