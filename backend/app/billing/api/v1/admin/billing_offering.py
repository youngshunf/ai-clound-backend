from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.billing_offering import (
    CreateBillingOfferingParam,
    DeleteBillingOfferingParam,
    GetBillingOfferingDetail,
    UpdateBillingOfferingParam,
)
from backend.app.billing.service.billing_offering_service import billing_offering_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）详情', dependencies=[DependsJwtAuth], name='billing_admin_get_billing_offering')
async def get_billing_offering(
    db: CurrentSession, pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')]
) -> ResponseSchemaModel[GetBillingOfferingDetail]:
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    return response_base.success(data=billing_offering)


@router.get(
    '',
    summary='分页获取所有商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='billing_admin_get_billing_offering_paginated',
)
async def get_billing_offering_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetBillingOfferingDetail]]:
    page_data = await billing_offering_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[
        Depends(RequestPermission('billing:offering:add')),
        DependsRBAC,
    ],
    name='billing_admin_create_billing_offering',
)
async def create_billing_offering(db: CurrentSessionTransaction, obj: CreateBillingOfferingParam) -> ResponseModel:
    await billing_offering_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[
        Depends(RequestPermission('billing:offering:edit')),
        DependsRBAC,
    ],
    name='billing_admin_update_billing_offering',
)
async def update_billing_offering(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')], obj: UpdateBillingOfferingParam
) -> ResponseModel:
    count = await billing_offering_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[
        Depends(RequestPermission('billing:offering:del')),
        DependsRBAC,
    ],
    name='billing_admin_delete_billing_offering',
)
async def delete_billing_offering(db: CurrentSessionTransaction, obj: DeleteBillingOfferingParam) -> ResponseModel:
    count = await billing_offering_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
