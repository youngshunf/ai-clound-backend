"""HASN 未读计数 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_unread_counts import (
    CreateHasnUnreadCountsParam,
    GetHasnUnreadCountsDetail,
    UpdateHasnUnreadCountsParam,
)
from backend.app.hasn.service.hasn_unread_counts_service import hasn_unread_counts_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的HASN 未读计数列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_unread_countss(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnUnreadCountsDetail]]:
    page_data = await hasn_unread_counts_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 未读计数',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_unread_counts(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnUnreadCountsParam,
) -> ResponseModel:
    result = await hasn_unread_counts_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 未读计数详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_unread_counts(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN 未读计数 ID')],
) -> ResponseSchemaModel[GetHasnUnreadCountsDetail]:
    hasn_unread_counts = await hasn_unread_counts_service.get(db=db, pk=pk)
    if hasn_unread_counts.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN 未读计数')
    return response_base.success(data=hasn_unread_counts)


@router.put(
    '/{pk}',
    summary='更新HASN 未读计数',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_unread_counts(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 未读计数 ID')],
    obj: UpdateHasnUnreadCountsParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_unread_counts = await hasn_unread_counts_service.get(db=db, pk=pk)
    if hasn_unread_counts.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN 未读计数')
    count = await hasn_unread_counts_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 未读计数',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_unread_counts(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 未读计数 ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_unread_counts = await hasn_unread_counts_service.get(db=db, pk=pk)
    if hasn_unread_counts.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN 未读计数')
    from backend.app.hasn.schema.hasn_unread_counts import DeleteHasnUnreadCountsParam
    count = await hasn_unread_counts_service.delete(db=db, obj=DeleteHasnUnreadCountsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
