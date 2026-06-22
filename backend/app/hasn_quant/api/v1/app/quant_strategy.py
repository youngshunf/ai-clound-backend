"""量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_quant.schema.quant_strategy import (
    CreateQuantStrategyParam,
    GetQuantStrategyDetail,
    UpdateQuantStrategyParam,
)
from backend.app.hasn_quant.service.quant_strategy_service import quant_strategy_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_quant_app_get_my_quant_strategy',
)
async def get_my_quant_strategy(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetQuantStrategyDetail]]:
    page_data = await quant_strategy_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_create_my_quant_strategy',
)
async def create_my_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateQuantStrategyParam,
) -> ResponseModel:
    result = await quant_strategy_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_get_my_quant_strategy_detail',
)
async def get_my_quant_strategy_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
) -> ResponseSchemaModel[GetQuantStrategyDetail]:
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    if quant_strategy.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    return response_base.success(data=quant_strategy)


@router.put(
    '/{pk}',
    summary='更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_update_my_quant_strategy',
)
async def update_my_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
    obj: UpdateQuantStrategyParam,
) -> ResponseModel:
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    if getattr(quant_strategy, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    count = await quant_strategy_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_quant_app_delete_my_quant_strategy',
)
async def delete_my_quant_strategy(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID')],
) -> ResponseModel:
    user_id = request.user.id
    quant_strategy = await quant_strategy_service.get(db=db, pk=pk)
    if quant_strategy.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）')
    from backend.app.hasn_quant.schema.quant_strategy import DeleteQuantStrategyParam
    count = await quant_strategy_service.delete(db=db, obj=DeleteQuantStrategyParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
