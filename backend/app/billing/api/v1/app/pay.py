"""App API — 用户端支付"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.billing.schema.pay_channel import GetPayChannelSimple
from backend.app.billing.schema.pay_contract import GetPayContractUserView
from backend.app.billing.schema.pay_order import (
    CreatePayOrderParam,
    CreatePayOrderResponse,
    GetPayOrderDetail,
    PayOrderStatusResponse,
)
from backend.app.billing.service.pay_channel_service import pay_channel_service
from backend.app.billing.service.pay_contract_service import pay_contract_service
from backend.app.billing.service.pay_order_service import pay_order_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/channels',
    summary='获取可用支付渠道列表',
    dependencies=[DependsJwtAuth],
)
async def get_available_channels(
    db: CurrentSession,
) -> ResponseSchemaModel[list[GetPayChannelSimple]]:
    channels = await pay_channel_service.get_active_channels(db=db)
    simple_list = [
        GetPayChannelSimple(id=ch.id, code=ch.code, name=ch.name)
        for ch in channels
    ]
    return response_base.success(data=simple_list)


@router.post(
    '/create',
    summary='创建支付订单',
    dependencies=[DependsJwtAuth],
)
async def create_pay_order(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreatePayOrderParam,
) -> ResponseSchemaModel[CreatePayOrderResponse]:
    user_id = request.user.id
    user_ip = request.client.host if request.client else None
    app_code = getattr(request.state, 'app_code', 'huanxing')
    result = await pay_order_service.create_order(
        db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
    )
    return response_base.success(data=result)


@router.get(
    '/status/{order_no}',
    summary='查询订单支付状态',
    dependencies=[DependsJwtAuth],
)
async def get_order_status(
    db: CurrentSession,
    order_no: Annotated[str, Path(description='商户订单号')],
) -> ResponseSchemaModel[PayOrderStatusResponse]:
    result = await pay_order_service.get_status(db=db, order_no=order_no)
    return response_base.success(data=result)


@router.post(
    '/cancel/{order_no}',
    summary='取消订单',
    dependencies=[DependsJwtAuth],
)
async def cancel_order(
    request: Request,
    db: CurrentSessionTransaction,
    order_no: Annotated[str, Path(description='商户订单号')],
) -> ResponseModel:
    user_id = request.user.id
    await pay_order_service.cancel_order(db=db, order_no=order_no, user_id=user_id)
    return response_base.success()


@router.get(
    '/orders',
    summary='我的订单列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_orders(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPayOrderDetail]]:
    user_id = request.user.id
    page_data = await pay_order_service.get_list(db=db, user_id=user_id)
    return response_base.success(data=page_data)


@router.get(
    '/contract',
    summary='获取我的签约状态',
    dependencies=[DependsJwtAuth],
)
async def get_my_contract(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[GetPayContractUserView]:
    user_id = request.user.id
    result = await pay_contract_service.get_user_contract(db=db, user_id=user_id)
    return response_base.success(data=result)


@router.post(
    '/contract/cancel',
    summary='取消自动续费',
    dependencies=[DependsJwtAuth],
)
async def cancel_auto_renew(
    request: Request,
    db: CurrentSessionTransaction,
) -> ResponseModel:
    user_id = request.user.id
    await pay_contract_service.cancel_contract(db=db, user_id=user_id)
    return response_base.success()
