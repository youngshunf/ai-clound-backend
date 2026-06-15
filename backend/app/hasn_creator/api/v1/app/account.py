"""平台账号（1:N project）；同一项目多平台真实账号 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.account import (
    CreateAccountParam,
    GetAccountDetail,
    UpdateAccountParam,
)
from backend.app.hasn_creator.service.account_service import account_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的平台账号（1:N project）；同一项目多平台真实账号列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_account',
)
async def get_my_account(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetAccountDetail]]:
    page_data = await account_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_account',
)
async def create_my_account(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateAccountParam,
) -> ResponseModel:
    result = await account_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取平台账号（1:N project）；同一项目多平台真实账号详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_account_detail',
)
async def get_my_account_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
) -> ResponseSchemaModel[GetAccountDetail]:
    account = await account_service.get(db=db, pk=pk)
    if account.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该平台账号（1:N project）；同一项目多平台真实账号')
    return response_base.success(data=account)


@router.put(
    '/{pk}',
    summary='更新平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_account',
)
async def update_my_account(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
    obj: UpdateAccountParam,
) -> ResponseModel:
    account = await account_service.get(db=db, pk=pk)
    if getattr(account, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该平台账号（1:N project）；同一项目多平台真实账号')
    count = await account_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_account',
)
async def delete_my_account(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
) -> ResponseModel:
    user_id = request.user.id
    account = await account_service.get(db=db, pk=pk)
    if account.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该平台账号（1:N project）；同一项目多平台真实账号')
    from backend.app.hasn_creator.schema.account import DeleteAccountParam
    count = await account_service.delete(db=db, obj=DeleteAccountParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
