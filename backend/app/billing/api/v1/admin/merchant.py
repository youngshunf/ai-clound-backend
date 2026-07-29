"""Admin API — 支付商户"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.schema.pay_merchant import (
    CreatePayMerchantParam,
    GetPayMerchantDetail,
    GetPayMerchantSimple,
    UpdatePayMerchantParam,
)
from backend.app.billing.service.pay_merchant_service import pay_merchant_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData, paging_data
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='分页获取商户列表',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_merchants_paginated')
async def get_merchants_paginated(
    db: CurrentSession,
    type: Annotated[str | None, Query(description='类型 weixin/alipay')] = None,
    status: Annotated[int | None, Query(description='状态')] = None,
) -> ResponseSchemaModel[PageData[GetPayMerchantDetail]]:
    select_stmt = await pay_merchant_dao.get_select(type_=type, status=status)
    page_data = await paging_data(db, select_stmt)
    return response_base.success(data=page_data)


@router.get(
    '/simple',
    summary='获取全部启用商户（下拉选择用）',
    dependencies=[DependsJwtAuth],
 name='admin_get_merchants_simple')
async def get_merchants_simple(
    db: CurrentSession,
) -> ResponseSchemaModel[list[GetPayMerchantSimple]]:
    merchants = await pay_merchant_service.get_all_active(db=db)
    result = [GetPayMerchantSimple.model_validate(m) for m in merchants]
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取商户详情',
    dependencies=[DependsJwtAuth],
 name='admin_get_merchant')
async def get_merchant(
    pk: Annotated[int, Path(description='商户 ID')],
    db: CurrentSession,
) -> ResponseSchemaModel[GetPayMerchantDetail]:
    merchant = await pay_merchant_service.get(db=db, pk=pk)
    if not merchant:
        raise errors.NotFoundError(msg='商户不存在')
    data = GetPayMerchantDetail.model_validate(merchant)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建商户',
    dependencies=[DependsJwtAuth],
 name='admin_create_merchant')
async def create_merchant(
    db: CurrentSessionTransaction,
    obj: CreatePayMerchantParam,
) -> ResponseModel:
    await pay_merchant_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新商户',
    dependencies=[DependsJwtAuth],
 name='admin_update_merchant')
async def update_merchant(
    pk: Annotated[int, Path(description='商户 ID')],
    obj: UpdatePayMerchantParam,
    db: CurrentSessionTransaction,
) -> ResponseModel:
    await pay_merchant_service.update(db=db, pk=pk, obj=obj)
    return response_base.success()


@router.delete(
    '',
    summary='删除商户',
    dependencies=[DependsJwtAuth],
 name='admin_delete_merchants')
async def delete_merchants(
    pk: Annotated[list[int], Query(...)],
    db: CurrentSessionTransaction,
) -> ResponseModel:
    await pay_merchant_service.delete(db=db, pk=pk)
    return response_base.success()
