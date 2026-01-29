from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.user_tier.schema.user_credit_balance import (
    CreateUserCreditBalanceParam,
    DeleteUserCreditBalanceParam,
    GetUserCreditBalanceDetail,
    UpdateUserCreditBalanceParam,
)
from backend.app.user_tier.service.user_credit_balance_service import user_credit_balance_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取用户积分余额详情', dependencies=[DependsJwtAuth])
async def get_user_credit_balance(
    db: CurrentSession, pk: Annotated[int, Path(description='用户积分余额 ID')]
) -> ResponseSchemaModel[GetUserCreditBalanceDetail]:
    user_credit_balance = await user_credit_balance_service.get(db=db, pk=pk)
    return response_base.success(data=user_credit_balance)


@router.get(
    '',
    summary='分页获取所有用户积分余额',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_user_credit_balances_paginated(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetUserCreditBalanceDetail]]:
    page_data = await user_credit_balance_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:add')),
        DependsRBAC,
    ],
)
async def create_user_credit_balance(db: CurrentSessionTransaction, obj: CreateUserCreditBalanceParam) -> ResponseModel:
    await user_credit_balance_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:edit')),
        DependsRBAC,
    ],
)
async def update_user_credit_balance(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='用户积分余额 ID')],
    obj: UpdateUserCreditBalanceParam,
) -> ResponseModel:
    count = await user_credit_balance_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:del')),
        DependsRBAC,
    ],
)
async def delete_user_credit_balances(
    db: CurrentSessionTransaction, obj: DeleteUserCreditBalanceParam
) -> ResponseModel:
    count = await user_credit_balance_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
