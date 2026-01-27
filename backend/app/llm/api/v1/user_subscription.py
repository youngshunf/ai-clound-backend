from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.llm.schema.user_subscription import (
    CreateUserSubscriptionParam,
    DeleteUserSubscriptionParam,
    GetUserSubscriptionDetail,
    UpdateUserSubscriptionParam,
)
from backend.app.llm.service.user_subscription_service import user_subscription_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取用户订阅表 - 管理用户的订阅等级和积分余额详情', dependencies=[DependsJwtAuth])
async def get_user_subscription(
    db: CurrentSession, pk: Annotated[int, Path(description='用户订阅表 - 管理用户的订阅等级和积分余额 ID')]
) -> ResponseSchemaModel[GetUserSubscriptionDetail]:
    user_subscription = await user_subscription_service.get(db=db, pk=pk)
    return response_base.success(data=user_subscription)


@router.get(
    '',
    summary='分页获取所有用户订阅表 - 管理用户的订阅等级和积分余额',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_user_subscriptions_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetUserSubscriptionDetail]]:
    page_data = await user_subscription_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建用户订阅表 - 管理用户的订阅等级和积分余额',
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
    summary='更新用户订阅表 - 管理用户的订阅等级和积分余额',
    dependencies=[
        Depends(RequestPermission('user:subscription:edit')),
        DependsRBAC,
    ],
)
async def update_user_subscription(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='用户订阅表 - 管理用户的订阅等级和积分余额 ID')], obj: UpdateUserSubscriptionParam
) -> ResponseModel:
    count = await user_subscription_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除用户订阅表 - 管理用户的订阅等级和积分余额',
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
