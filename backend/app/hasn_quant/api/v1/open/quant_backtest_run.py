"""回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_quant.schema.quant_backtest_run import GetQuantBacktestRunDetail
from backend.app.hasn_quant.service.quant_backtest_run_service import quant_backtest_run_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）列表',
    dependencies=[DependsPagination],
    name='hasn_quant_open_get_quant_backtest_run',
)
async def get_quant_backtest_run(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetQuantBacktestRunDetail]]:
    page_data = await quant_backtest_run_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）详情',
    name='hasn_quant_open_get_quant_backtest_run_detail',
)
async def get_quant_backtest_run_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
) -> ResponseSchemaModel[GetQuantBacktestRunDetail]:
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    return response_base.success(data=quant_backtest_run)
