"""回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_quant.schema.quant_backtest_run import (
    CreateQuantBacktestRunParam,
    GetQuantBacktestRunDetail,
    UpdateQuantBacktestRunParam,
)
from backend.app.hasn_quant.service.quant_backtest_run_service import quant_backtest_run_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_quant_app_get_my_quant_backtest_run',
)
async def get_my_quant_backtest_run(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetQuantBacktestRunDetail]]:
    page_data = await quant_backtest_run_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_create_my_quant_backtest_run',
)
async def create_my_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateQuantBacktestRunParam,
) -> ResponseModel:
    result = await quant_backtest_run_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_get_my_quant_backtest_run_detail',
)
async def get_my_quant_backtest_run_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
) -> ResponseSchemaModel[GetQuantBacktestRunDetail]:
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    if quant_backtest_run.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    return response_base.success(data=quant_backtest_run)


@router.put(
    '/{pk}',
    summary='更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_update_my_quant_backtest_run',
)
async def update_my_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
    obj: UpdateQuantBacktestRunParam,
) -> ResponseModel:
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    if getattr(quant_backtest_run, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    count = await quant_backtest_run_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_delete_my_quant_backtest_run',
)
async def delete_my_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
) -> ResponseModel:
    user_id = request.user.id
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    if quant_backtest_run.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    from backend.app.hasn_quant.schema.quant_backtest_run import DeleteQuantBacktestRunParam
    count = await quant_backtest_run_service.delete(db=db, obj=DeleteQuantBacktestRunParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
