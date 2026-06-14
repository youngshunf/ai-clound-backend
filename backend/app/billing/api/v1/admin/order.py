"""Admin API — 订单管理"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.billing.schema.pay_order import GetPayOrderDetail
from backend.app.billing.service.pay_order_service import pay_order_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='分页获取订单列表（管理端）',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_orders_paginated')
async def get_orders_paginated(
    db: CurrentSession,
    user_id: Annotated[int | None, Query(description='按用户筛选')] = None,
    status: Annotated[int | None, Query(description='按状态筛选')] = None,
) -> ResponseSchemaModel[PageData[GetPayOrderDetail]]:
    page_data = await pay_order_service.get_list(db=db, user_id=user_id, status=status)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取订单详情',
    dependencies=[DependsJwtAuth],
 name='admin_get_order_detail')
async def get_order_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='订单 ID')],
) -> ResponseSchemaModel[GetPayOrderDetail]:
    order = await pay_order_service.get(db=db, pk=pk)
    return response_base.success(data=order)
