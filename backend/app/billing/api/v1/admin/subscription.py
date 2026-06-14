from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.user_subscription import (
    CreateUserSubscriptionParam,
    DeleteUserSubscriptionParam,
    GetUserSubscriptionDetail,
    UpdateUserSubscriptionParam,
)
from backend.app.billing.service.user_subscription_service import user_subscription_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取用户订阅详情', dependencies=[DependsJwtAuth], name='admin_get_user_subscription')
async def get_user_subscription(
    db: CurrentSession, pk: Annotated[int, Path(description='用户订阅 ID')]
) -> ResponseSchemaModel[GetUserSubscriptionDetail]:
    user_subscription = await user_subscription_service.get(db=db, pk=pk)
    return response_base.success(data=user_subscription)


@router.get(
    '',
    summary='分页获取所有用户订阅',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_user_subscriptions_paginated')
async def get_user_subscriptions_paginated(
    db: CurrentSession,
    user_keyword: Annotated[str | None, Query(description='用户昵称/手机号搜索')] = None,
    billing_cycle_start: Annotated[list[date] | None, Query(description='计费周期开始时间范围')] = None,
    billing_cycle_end: Annotated[list[date] | None, Query(description='计费周期结束时间范围')] = None,
    status: Annotated[str | None, Query(description='订阅状态')] = None,
) -> ResponseSchemaModel[PageData[GetUserSubscriptionDetail]]:
    page_data = await user_subscription_service.get_list(
        db=db,
        user_keyword=user_keyword,
        billing_cycle_start=billing_cycle_start,
        billing_cycle_end=billing_cycle_end,
        status=status,
    )
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建用户订阅',
    dependencies=[
        Depends(RequestPermission('user:subscription:add')),
        DependsRBAC,
    ],
)
async def create_user_subscription(db: CurrentSessionTransaction, obj: CreateUserSubscriptionParam) -> ResponseModel:
    await user_subscription_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新用户订阅',
    dependencies=[
        Depends(RequestPermission('user:subscription:edit')),
        DependsRBAC,
    ],
)
async def update_user_subscription(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='用户订阅 ID')], obj: UpdateUserSubscriptionParam
) -> ResponseModel:
    count = await user_subscription_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除用户订阅',
    dependencies=[
        Depends(RequestPermission('user:subscription:del')),
        DependsRBAC,
    ],
)
async def delete_user_subscription(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='用户订阅 ID')]
) -> ResponseModel:
    count = await user_subscription_service.delete(db=db, obj=DeleteUserSubscriptionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除用户订阅',
    dependencies=[
        Depends(RequestPermission('user:subscription:del')),
        DependsRBAC,
    ],
)
async def delete_user_subscriptions(db: CurrentSessionTransaction, obj: DeleteUserSubscriptionParam) -> ResponseModel:
    count = await user_subscription_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
