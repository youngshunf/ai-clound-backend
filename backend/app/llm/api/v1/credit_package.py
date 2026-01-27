from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.llm.schema.credit_package import (
    CreateCreditPackageParam,
    DeleteCreditPackageParam,
    GetCreditPackageDetail,
    UpdateCreditPackageParam,
)
from backend.app.llm.service.credit_package_service import credit_package_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取积分包配置表 - 定义可购买的积分包详情', dependencies=[DependsJwtAuth])
async def get_credit_package(
    db: CurrentSession, pk: Annotated[int, Path(description='积分包配置表 - 定义可购买的积分包 ID')]
) -> ResponseSchemaModel[GetCreditPackageDetail]:
    credit_package = await credit_package_service.get(db=db, pk=pk)
    return response_base.success(data=credit_package)


@router.get(
    '',
    summary='分页获取所有积分包配置表 - 定义可购买的积分包',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_credit_packages_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetCreditPackageDetail]]:
    page_data = await credit_package_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建积分包配置表 - 定义可购买的积分包',
    dependencies=[
        Depends(RequestPermission('credit:package:add')),
        DependsRBAC,
    ],
)
async def create_credit_package(db: CurrentSessionTransaction, obj: CreateCreditPackageParam) -> ResponseModel:
    await credit_package_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新积分包配置表 - 定义可购买的积分包',
    dependencies=[
        Depends(RequestPermission('credit:package:edit')),
        DependsRBAC,
    ],
)
async def update_credit_package(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='积分包配置表 - 定义可购买的积分包 ID')], obj: UpdateCreditPackageParam
) -> ResponseModel:
    count = await credit_package_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除积分包配置表 - 定义可购买的积分包',
    dependencies=[
        Depends(RequestPermission('credit:package:del')),
        DependsRBAC,
    ],
)
async def delete_credit_packages(db: CurrentSessionTransaction, obj: DeleteCreditPackageParam) -> ResponseModel:
    count = await credit_package_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
