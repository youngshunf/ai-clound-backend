from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_platform_operator_grants import (
    CreateHasnPlatformOperatorGrantsParam,
    DeleteHasnPlatformOperatorGrantsParam,
    GetHasnPlatformOperatorGrantsDetail,
    UpdateHasnPlatformOperatorGrantsParam,
)
from backend.app.hasn.service.hasn_platform_operator_grants_service import hasn_platform_operator_grants_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取平台运维授予源（Admin-only·G1 特权门）详情', dependencies=[DependsJwtAuth], name='hasn_admin_get_hasn_platform_operator_grants')
async def get_hasn_platform_operator_grants(
    db: CurrentSession, pk: Annotated[int, Path(description='平台运维授予源（Admin-only·G1 特权门） ID')]
) -> ResponseSchemaModel[GetHasnPlatformOperatorGrantsDetail]:
    hasn_platform_operator_grants = await hasn_platform_operator_grants_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_platform_operator_grants)


@router.get(
    '',
    summary='分页获取所有平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_admin_get_hasn_platform_operator_grants_paginated',
)
async def get_hasn_platform_operator_grants_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnPlatformOperatorGrantsDetail]]:
    page_data = await hasn_platform_operator_grants_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:add')),
        DependsRBAC,
    ],
    name='hasn_admin_create_hasn_platform_operator_grants',
)
async def create_hasn_platform_operator_grants(db: CurrentSessionTransaction, obj: CreateHasnPlatformOperatorGrantsParam) -> ResponseModel:
    await hasn_platform_operator_grants_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:edit')),
        DependsRBAC,
    ],
    name='hasn_admin_update_hasn_platform_operator_grants',
)
async def update_hasn_platform_operator_grants(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台运维授予源（Admin-only·G1 特权门） ID')], obj: UpdateHasnPlatformOperatorGrantsParam
) -> ResponseModel:
    count = await hasn_platform_operator_grants_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:del')),
        DependsRBAC,
    ],
    name='hasn_admin_delete_hasn_platform_operator_grants',
)
async def delete_hasn_platform_operator_grants(db: CurrentSessionTransaction, obj: DeleteHasnPlatformOperatorGrantsParam) -> ResponseModel:
    count = await hasn_platform_operator_grants_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
