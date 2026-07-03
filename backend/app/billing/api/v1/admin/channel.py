"""Admin API — 支付渠道管理"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.billing.schema.pay_channel import (
    CreatePayChannelParam,
    DeletePayChannelParam,
    GetPayChannelDetail,
    UpdatePayChannelParam,
    UpdatePayChannelStatusParam,
)
from backend.app.billing.service.pay_channel_service import pay_channel_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取支付渠道详情', dependencies=[DependsJwtAuth], name='admin_get_pay_channel')
async def get_pay_channel(
    db: CurrentSession, pk: Annotated[int, Path(description='支付渠道 ID')]
) -> ResponseSchemaModel[GetPayChannelDetail]:
    channel = await pay_channel_service.get(db=db, pk=pk)
    return response_base.success(data=channel)


@router.get(
    '',
    summary='分页获取支付渠道',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_pay_channels_paginated')
async def get_pay_channels_paginated(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPayChannelDetail]]:
    page_data = await pay_channel_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建支付渠道',
    dependencies=[
        Depends(RequestPermission('pay:channel:add')),
        DependsRBAC,
    ],
)
async def create_pay_channel(db: CurrentSessionTransaction, obj: CreatePayChannelParam) -> ResponseModel:
    await pay_channel_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新支付渠道',
    dependencies=[
        Depends(RequestPermission('pay:channel:edit')),
        DependsRBAC,
    ],
)
async def update_pay_channel(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='支付渠道 ID')],
    obj: UpdatePayChannelParam,
) -> ResponseModel:
    count = await pay_channel_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.patch(
    '/{pk}/status',
    summary='更新支付渠道状态',
    dependencies=[
        Depends(RequestPermission('pay:channel:edit')),
        DependsRBAC,
    ],
)
async def update_pay_channel_status(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='支付渠道 ID')],
    obj: UpdatePayChannelStatusParam,
) -> ResponseModel:
    count = await pay_channel_service.update_status(db=db, pk=pk, status=obj.status)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除支付渠道',
    dependencies=[
        Depends(RequestPermission('pay:channel:del')),
        DependsRBAC,
    ],
)
async def delete_pay_channels(db: CurrentSessionTransaction, obj: DeletePayChannelParam) -> ResponseModel:
    count = await pay_channel_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
