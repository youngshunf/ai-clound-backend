from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_quant.schema.quant_backtest_run import (
    CreateQuantBacktestRunParam,
    DeleteQuantBacktestRunParam,
    GetQuantBacktestRunDetail,
    UpdateQuantBacktestRunParam,
)
from backend.app.hasn_quant.service.quant_backtest_run_service import quant_backtest_run_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）详情', dependencies=[DependsJwtAuth], name='hasn_quant_admin_get_quant_backtest_run')
async def get_quant_backtest_run(
    db: CurrentSession, pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')]
) -> ResponseSchemaModel[GetQuantBacktestRunDetail]:
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    return response_base.success(data=quant_backtest_run)


@router.get(
    '',
    summary='分页获取所有回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_quant_admin_get_quant_backtest_run_paginated',
)
async def get_quant_backtest_run_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetQuantBacktestRunDetail]]:
    page_data = await quant_backtest_run_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[
        Depends(RequestPermission('quant:backtest:run:add')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_create_quant_backtest_run',
)
async def create_quant_backtest_run(db: CurrentSessionTransaction, obj: CreateQuantBacktestRunParam) -> ResponseModel:
    await quant_backtest_run_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[
        Depends(RequestPermission('quant:backtest:run:edit')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_update_quant_backtest_run',
)
async def update_quant_backtest_run(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')], obj: UpdateQuantBacktestRunParam
) -> ResponseModel:
    count = await quant_backtest_run_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[
        Depends(RequestPermission('quant:backtest:run:del')),
        DependsRBAC,
    ],
    name='hasn_quant_admin_delete_quant_backtest_run',
)
async def delete_quant_backtest_run(db: CurrentSessionTransaction, obj: DeleteQuantBacktestRunParam) -> ResponseModel:
    count = await quant_backtest_run_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
