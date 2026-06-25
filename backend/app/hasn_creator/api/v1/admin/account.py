from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.account import (
    CreateAccountParam,
    DeleteAccountParam,
    GetAccountDetail,
    UpdateAccountParam,
)
from backend.app.hasn_creator.service.account_service import account_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取平台账号（1:N project）；同一项目多平台真实账号详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_account')
async def get_account(
    db: CurrentSession, pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')]
) -> ResponseSchemaModel[GetAccountDetail]:
    account = await account_service.get(db=db, pk=pk)
    return response_base.success(data=account)


@router.get(
    '',
    summary='分页获取所有平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_account_paginated',
)
async def get_account_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetAccountDetail]]:
    page_data = await account_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[
        Depends(RequestPermission('account:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_account',
)
async def create_account(db: CurrentSessionTransaction, obj: CreateAccountParam) -> ResponseModel:
    await account_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[
        Depends(RequestPermission('account:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_account',
)
async def update_account(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')], obj: UpdateAccountParam
) -> ResponseModel:
    count = await account_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台账号（1:N project）；同一项目多平台真实账号',
    dependencies=[
        Depends(RequestPermission('account:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_account',
)
async def delete_account(db: CurrentSessionTransaction, obj: DeleteAccountParam) -> ResponseModel:
    count = await account_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
