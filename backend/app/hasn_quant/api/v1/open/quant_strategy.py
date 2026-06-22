"""量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_quant.schema.quant_strategy import GetQuantStrategyDetail
from backend.app.hasn_quant.service.quant_strategy_service import quant_strategy_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）列表',
    dependencies=[DependsPagination],
    name='hasn_quant_open_get_quant_strategy',
)
async def get_quant_strategy(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetQuantStrategyDetail]]:
    page_data = await quant_strategy_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）详情',
    name='hasn_quant_open_get_quant_strategy_detail',
)
async def get_quant_strategy_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
) -> ResponseSchemaModel[GetQuantStrategyDetail]:
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    return response_base.success(data=quant_strategy)
