from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.billing_plan import (
    CreateBillingPlanParam,
    DeleteBillingPlanParam,
    GetBillingPlanDetail,
    UpdateBillingPlanParam,
)
from backend.app.billing.service.billing_plan_service import billing_plan_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取商品档位（价格+配额快照+试用/宽限策略）详情', dependencies=[DependsJwtAuth], name='billing_admin_get_billing_plan')
async def get_billing_plan(
    db: CurrentSession, pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')]
) -> ResponseSchemaModel[GetBillingPlanDetail]:
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    return response_base.success(data=billing_plan)


@router.get(
    '',
    summary='分页获取所有商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='billing_admin_get_billing_plan_paginated',
)
async def get_billing_plan_paginated(
    db: CurrentSession,
    offering_key: Annotated[str | None, Query(description='按所属 offering 业务键过滤（查某商品的全部档位）')] = None,
    status: Annotated[str | None, Query(description='按上/下架状态过滤（active/inactive）')] = None,
) -> ResponseSchemaModel[PageData[GetBillingPlanDetail]]:
    page_data = await billing_plan_service.get_list(db=db, offering_key=offering_key, status=status)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[
        Depends(RequestPermission('billing:plan:add')),
        DependsRBAC,
    ],
    name='billing_admin_create_billing_plan',
)
async def create_billing_plan(db: CurrentSessionTransaction, obj: CreateBillingPlanParam) -> ResponseModel:
    await billing_plan_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[
        Depends(RequestPermission('billing:plan:edit')),
        DependsRBAC,
    ],
    name='billing_admin_update_billing_plan',
)
async def update_billing_plan(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')], obj: UpdateBillingPlanParam
) -> ResponseModel:
    count = await billing_plan_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[
        Depends(RequestPermission('billing:plan:del')),
        DependsRBAC,
    ],
    name='billing_admin_delete_billing_plan',
)
async def delete_billing_plan(db: CurrentSessionTransaction, obj: DeleteBillingPlanParam) -> ResponseModel:
    count = await billing_plan_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
