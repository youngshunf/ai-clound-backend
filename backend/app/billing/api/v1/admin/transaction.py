from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.credit_transaction import (
    CreateCreditTransactionParam,
    DeleteCreditTransactionParam,
    GetCreditTransactionDetail,
    UpdateCreditTransactionParam,
)
from backend.app.billing.service.credit_transaction_service import credit_transaction_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取积分交易记录详情', dependencies=[DependsJwtAuth], name='admin_get_credit_transaction')
async def get_credit_transaction(
    db: CurrentSession, pk: Annotated[int, Path(description='积分交易记录 ID')]
) -> ResponseSchemaModel[GetCreditTransactionDetail]:
    credit_transaction = await credit_transaction_service.get(db=db, pk=pk)
    return response_base.success(data=credit_transaction)


@router.get(
    '',
    summary='分页获取所有积分交易记录',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_credit_transactions_paginated')
async def get_credit_transactions_paginated(
    db: CurrentSession,
    user_keyword: Annotated[str | None, Query(description='用户昵称/手机号搜索')] = None,
    transaction_type: Annotated[str | None, Query(description='交易类型')] = None,
    reference_id: Annotated[str | None, Query(description='关联 ID')] = None,
    reference_type: Annotated[str | None, Query(description='关联类型')] = None,
) -> ResponseSchemaModel[PageData[GetCreditTransactionDetail]]:
    page_data = await credit_transaction_service.get_list(
        db=db,
        user_keyword=user_keyword,
        transaction_type=transaction_type,
        reference_id=reference_id,
        reference_type=reference_type,
    )
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建积分交易记录',
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
    summary='更新积分交易记录',
    dependencies=[
        Depends(RequestPermission('credit:transaction:edit')),
        DependsRBAC,
    ],
)
async def update_credit_transaction(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='积分交易记录 ID')], obj: UpdateCreditTransactionParam
) -> ResponseModel:
    count = await credit_transaction_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除积分交易记录',
    dependencies=[
        Depends(RequestPermission('credit:transaction:del')),
        DependsRBAC,
    ],
)
async def delete_credit_transaction(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='积分交易记录 ID')]
) -> ResponseModel:
    count = await credit_transaction_service.delete(db=db, obj=DeleteCreditTransactionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除积分交易记录',
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
