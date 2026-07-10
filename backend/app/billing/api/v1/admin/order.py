"""Admin API — 订单管理"""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Query

from backend.app.billing.schema.pay_order import (
    GetPayOrderDetail,
    GetPayRefundDetail,
    RefundOrderParam,
    RefundOrderResponse,
)
from backend.app.billing.service.pay_order_service import pay_order_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

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


@router.get(
    '/refunds',
    summary='分页获取退款记录（管理端）',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_refunds_paginated')
async def get_refunds_paginated(
    db: CurrentSession,
    order_no: Annotated[str | None, Query(description='按订单号筛选')] = None,
) -> ResponseSchemaModel[PageData[GetPayRefundDetail]]:
    page_data = await pay_order_service.get_refund_list(db=db, order_no=order_no)
    return response_base.success(data=page_data)


@router.post(
    '/{order_no}/refund',
    summary='订单退款（管理端·MK-9 退款编排）',
    dependencies=[DependsJwtAuth],
 name='admin_refund_order')
async def refund_order(
    db: CurrentSessionTransaction,
    order_no: Annotated[str, Path(description='商户订单号')],
    obj: Annotated[RefundOrderParam, Body(default_factory=RefundOrderParam)],
) -> ResponseSchemaModel[RefundOrderResponse]:
    """管理端发起退款：回收权益/额度（fail-closed）→ 渠道退款 → 订单状态→已退款（单事务原子）。

    幂等：订单已退款直接回既有退款记录。0.01 元真钱退款人验仍属福仔专项（涉动钱），本端点是其执行入口。
    """
    result = await pay_order_service.refund_order(
        db=db,
        order_no=order_no,
        reason=obj.reason or '',
        refund_amount=obj.refund_amount,
    )
    return response_base.success(data=result)
