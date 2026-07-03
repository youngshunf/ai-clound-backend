from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.admin.schema.test_user import (
    CreateTestUserParam,
    DeleteTestUserParam,
    GetTestUserDetail,
    UpdateTestUserParam,
)
from backend.app.admin.service.test_user_service import test_user_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取测试用户详情', dependencies=[DependsJwtAuth])
async def get_test_user(
    db: CurrentSession, pk: Annotated[int, Path(description='测试用户 ID')]
) -> ResponseSchemaModel[GetTestUserDetail]:
    test_user = await test_user_service.get(db=db, pk=pk)
    return response_base.success(data=test_user)


@router.get(
    '',
    summary='分页获取所有测试用户',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_test_users_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetTestUserDetail]]:
    page_data = await test_user_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建测试用户',
    dependencies=[
        Depends(RequestPermission('test:user:add')),
        DependsRBAC,
    ],
)
async def create_test_user(db: CurrentSessionTransaction, obj: CreateTestUserParam) -> ResponseModel:
    await test_user_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新测试用户',
    dependencies=[
        Depends(RequestPermission('test:user:edit')),
        DependsRBAC,
    ],
)
async def update_test_user(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='测试用户 ID')], obj: UpdateTestUserParam
) -> ResponseModel:
    count = await test_user_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除测试用户',
    dependencies=[
        Depends(RequestPermission('test:user:del')),
        DependsRBAC,
    ],
)
async def delete_test_users(db: CurrentSessionTransaction, obj: DeleteTestUserParam) -> ResponseModel:
    count = await test_user_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
