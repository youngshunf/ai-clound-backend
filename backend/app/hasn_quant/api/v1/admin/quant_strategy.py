from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_quant.schema.quant_strategy import (
    CreateQuantStrategyParam,
    DeleteQuantStrategyParam,
    GetQuantStrategyDetail,
    UpdateQuantStrategyParam,
)
from backend.app.hasn_quant.service.quant_strategy_service import quant_strategy_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）详情', dependencies=[DependsJwtAuth], name='hasn_quant_admin_get_quant_strategy')
async def get_quant_strategy(
    db: CurrentSession, pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')]
) -> ResponseSchemaModel[GetQuantStrategyDetail]:
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    return response_base.success(data=quant_strategy)


@router.get(
    '',
    summary='分页获取所有量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_quant_admin_get_quant_strategy_paginated',
)
async def get_quant_strategy_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetQuantStrategyDetail]]:
    page_data = await quant_strategy_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[
        Depends(RequestPermission('quant:strategy:add')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_create_quant_strategy',
)
async def create_quant_strategy(db: CurrentSessionTransaction, obj: CreateQuantStrategyParam) -> ResponseModel:
    await quant_strategy_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[
        Depends(RequestPermission('quant:strategy:edit')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_update_quant_strategy',
)
async def update_quant_strategy(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')], obj: UpdateQuantStrategyParam
) -> ResponseModel:
    count = await quant_strategy_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[
        Depends(RequestPermission('quant:strategy:del')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_delete_quant_strategy',
)
async def delete_quant_strategy(db: CurrentSessionTransaction, obj: DeleteQuantStrategyParam) -> ResponseModel:
    count = await quant_strategy_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
