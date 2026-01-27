from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.llm.schema.model_credit_rate import (
    CreateModelCreditRateParam,
    DeleteModelCreditRateParam,
    GetModelCreditRateDetail,
    UpdateModelCreditRateParam,
)
from backend.app.llm.service.model_credit_rate_service import model_credit_rate_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取模型积分费率表 - 定义不同模型的积分消耗规则详情', dependencies=[DependsJwtAuth])
async def get_model_credit_rate(
    db: CurrentSession, pk: Annotated[int, Path(description='模型积分费率表 - 定义不同模型的积分消耗规则 ID')]
) -> ResponseSchemaModel[GetModelCreditRateDetail]:
    model_credit_rate = await model_credit_rate_service.get(db=db, pk=pk)
    return response_base.success(data=model_credit_rate)


@router.get(
    '',
    summary='分页获取所有模型积分费率表 - 定义不同模型的积分消耗规则',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_model_credit_rates_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetModelCreditRateDetail]]:
    page_data = await model_credit_rate_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建模型积分费率表 - 定义不同模型的积分消耗规则',
    dependencies=[
        Depends(RequestPermission('model:credit:rate:add')),
        DependsRBAC,
    ],
)
async def create_model_credit_rate(db: CurrentSessionTransaction, obj: CreateModelCreditRateParam) -> ResponseModel:
    await model_credit_rate_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新模型积分费率表 - 定义不同模型的积分消耗规则',
    dependencies=[
        Depends(RequestPermission('model:credit:rate:edit')),
        DependsRBAC,
    ],
)
async def update_model_credit_rate(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='模型积分费率表 - 定义不同模型的积分消耗规则 ID')], obj: UpdateModelCreditRateParam
) -> ResponseModel:
    count = await model_credit_rate_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除模型积分费率表 - 定义不同模型的积分消耗规则',
    dependencies=[
        Depends(RequestPermission('model:credit:rate:del')),
        DependsRBAC,
    ],
)
async def delete_model_credit_rates(db: CurrentSessionTransaction, obj: DeleteModelCreditRateParam) -> ResponseModel:
    count = await model_credit_rate_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
