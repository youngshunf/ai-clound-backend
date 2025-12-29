"""供应商管理 API"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.llm.schema.provider import (
    CreateProviderParam,
    GetProviderDetail,
    GetProviderList,
    UpdateProviderParam,
)
from backend.app.llm.service.provider_service import provider_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取供应商列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_provider_list(
    db: CurrentSession,
    name: Annotated[str | None, Query(description='供应商名称')] = None,
    enabled: Annotated[bool | None, Query(description='是否启用')] = None,
) -> ResponseSchemaModel[PageData[GetProviderList]]:
    page_data = await provider_service.get_list(db, name=name, enabled=enabled)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取供应商详情',
    dependencies=[DependsJwtAuth],
)
async def get_provider_detail(db: CurrentSession, pk: int) -> ResponseSchemaModel[GetProviderDetail]:
    data = await provider_service.get_detail(db, pk)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建供应商',
    dependencies=[
        Depends(RequestPermission('llm:provider:add')),
        DependsRBAC,
    ],
)
async def create_provider(db: CurrentSession, obj: CreateProviderParam) -> ResponseSchemaModel:
    await provider_service.create(db, obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新供应商',
    dependencies=[
        Depends(RequestPermission('llm:provider:edit')),
        DependsRBAC,
    ],
)
async def update_provider(db: CurrentSession, pk: int, obj: UpdateProviderParam) -> ResponseSchemaModel:
    await provider_service.update(db, pk, obj)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除供应商',
    dependencies=[
        Depends(RequestPermission('llm:provider:del')),
        DependsRBAC,
    ],
)
async def delete_provider(db: CurrentSession, pk: int) -> ResponseSchemaModel:
    await provider_service.delete(db, pk)
    return response_base.success()
