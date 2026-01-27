from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.llm.schema.credit_transaction import (
    CreateCreditTransactionParam,
    DeleteCreditTransactionParam,
    GetCreditTransactionDetail,
    UpdateCreditTransactionParam,
)
from backend.app.llm.service.credit_transaction_service import credit_transaction_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取积分交易记录表 - 审计所有积分变动详情', dependencies=[DependsJwtAuth])
async def get_credit_transaction(
    db: CurrentSession, pk: Annotated[int, Path(description='积分交易记录表 - 审计所有积分变动 ID')]
) -> ResponseSchemaModel[GetCreditTransactionDetail]:
    credit_transaction = await credit_transaction_service.get(db=db, pk=pk)
    return response_base.success(data=credit_transaction)


@router.get(
    '',
    summary='分页获取所有积分交易记录表 - 审计所有积分变动',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_credit_transactions_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetCreditTransactionDetail]]:
    page_data = await credit_transaction_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建积分交易记录表 - 审计所有积分变动',
    dependencies=[
        Depends(RequestPermission('credit:transaction:add')),
        DependsRBAC,
    ],
)
async def create_credit_transaction(db: CurrentSessionTransaction, obj: CreateCreditTransactionParam) -> ResponseModel:
    await credit_transaction_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新积分交易记录表 - 审计所有积分变动',
    dependencies=[
        Depends(RequestPermission('credit:transaction:edit')),
        DependsRBAC,
    ],
)
async def update_credit_transaction(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='积分交易记录表 - 审计所有积分变动 ID')], obj: UpdateCreditTransactionParam
) -> ResponseModel:
    count = await credit_transaction_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除积分交易记录表 - 审计所有积分变动',
    dependencies=[
        Depends(RequestPermission('credit:transaction:del')),
        DependsRBAC,
    ],
)
async def delete_credit_transactions(db: CurrentSessionTransaction, obj: DeleteCreditTransactionParam) -> ResponseModel:
    count = await credit_transaction_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
