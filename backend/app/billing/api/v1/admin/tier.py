from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.subscription_tier import (
    CreateSubscriptionTierParam,
    DeleteSubscriptionTierParam,
    GetSubscriptionTierDetail,
    UpdateSubscriptionTierParam,
)
from backend.app.billing.service.subscription_tier_service import subscription_tier_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取订阅等级配置详情', dependencies=[DependsJwtAuth], name='admin_get_subscription_tier')
async def get_subscription_tier(
    db: CurrentSession, pk: Annotated[int, Path(description='订阅等级配置 ID')]
) -> ResponseSchemaModel[GetSubscriptionTierDetail]:
    subscription_tier = await subscription_tier_service.get(db=db, pk=pk)
    return response_base.success(data=subscription_tier)


@router.get(
    '',
    summary='分页获取所有订阅等级配置',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_subscription_tiers_paginated')
async def get_subscription_tiers_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetSubscriptionTierDetail]]:
    page_data = await subscription_tier_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建订阅等级配置',
    dependencies=[
        Depends(RequestPermission('subscription:tier:add')),
        DependsRBAC,
    ],
)
async def create_subscription_tier(db: CurrentSessionTransaction, obj: CreateSubscriptionTierParam) -> ResponseModel:
    await subscription_tier_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新订阅等级配置',
    dependencies=[
        Depends(RequestPermission('subscription:tier:edit')),
        DependsRBAC,
    ],
)
async def update_subscription_tier(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='订阅等级配置 ID')], obj: UpdateSubscriptionTierParam
) -> ResponseModel:
    count = await subscription_tier_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除订阅等级配置',
    dependencies=[
        Depends(RequestPermission('subscription:tier:del')),
        DependsRBAC,
    ],
)
async def delete_subscription_tier(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='订阅等级配置 ID')]
) -> ResponseModel:
    count = await subscription_tier_service.delete(db=db, obj=DeleteSubscriptionTierParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除订阅等级配置',
    dependencies=[
        Depends(RequestPermission('subscription:tier:del')),
        DependsRBAC,
    ],
)
async def delete_subscription_tiers(db: CurrentSessionTransaction, obj: DeleteSubscriptionTierParam) -> ResponseModel:
    count = await subscription_tier_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
