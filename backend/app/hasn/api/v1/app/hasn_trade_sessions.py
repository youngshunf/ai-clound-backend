"""HASN 交易会话 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_trade_sessions import (
    CreateHasnTradeSessionsParam,
    GetHasnTradeSessionsDetail,
    UpdateHasnTradeSessionsParam,
)
from backend.app.hasn.service.hasn_trade_sessions_service import hasn_trade_sessions_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的HASN 交易会话列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_trade_sessionss(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnTradeSessionsDetail]]:
    page_data = await hasn_trade_sessions_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 交易会话',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_trade_sessions(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnTradeSessionsParam,
) -> ResponseModel:
    result = await hasn_trade_sessions_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 交易会话详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_trade_sessions(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN 交易会话 ID')],
) -> ResponseSchemaModel[GetHasnTradeSessionsDetail]:
    hasn_trade_sessions = await hasn_trade_sessions_service.get(db=db, pk=pk)
    if hasn_trade_sessions.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN 交易会话')
    return response_base.success(data=hasn_trade_sessions)


@router.put(
    '/{pk}',
    summary='更新HASN 交易会话',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_trade_sessions(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 交易会话 ID')],
    obj: UpdateHasnTradeSessionsParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_trade_sessions = await hasn_trade_sessions_service.get(db=db, pk=pk)
    if hasn_trade_sessions.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN 交易会话')
    count = await hasn_trade_sessions_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 交易会话',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_trade_sessions(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 交易会话 ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_trade_sessions = await hasn_trade_sessions_service.get(db=db, pk=pk)
    if hasn_trade_sessions.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN 交易会话')
    from backend.app.hasn.schema.hasn_trade_sessions import DeleteHasnTradeSessionsParam
    count = await hasn_trade_sessions_service.delete(db=db, obj=DeleteHasnTradeSessionsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
