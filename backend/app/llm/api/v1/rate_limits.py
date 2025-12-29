"""速率限制配置管理 API

@author Ysf
@date 2025-12-28
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.llm.schema.rate_limit import (
    CreateRateLimitConfigParam,
    GetRateLimitConfigDetail,
    GetRateLimitConfigList,
    UpdateRateLimitConfigParam,
)
from backend.app.llm.service.rate_limit_service import rate_limit_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取速率限制配置列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_rate_limit_list(
    db: CurrentSession,
    name: Annotated[str | None, Query(description='配置名称')] = None,
    enabled: Annotated[bool | None, Query(description='是否启用')] = None,
) -> ResponseSchemaModel[PageData[GetRateLimitConfigList]]:
    page_data = await rate_limit_service.get_list(db, name=name, enabled=enabled)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取速率限制配置详情',
    dependencies=[DependsJwtAuth],
)
async def get_rate_limit_detail(db: CurrentSession, pk: int) -> ResponseSchemaModel[GetRateLimitConfigDetail]:
    data = await rate_limit_service.get_detail(db, pk)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建速率限制配置',
    dependencies=[
        Depends(RequestPermission('llm:rate-limit:add')),
        DependsRBAC,
    ],
)
async def create_rate_limit(db: CurrentSession, obj: CreateRateLimitConfigParam) -> ResponseSchemaModel:
    await rate_limit_service.create(db, obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新速率限制配置',
    dependencies=[
        Depends(RequestPermission('llm:rate-limit:edit')),
        DependsRBAC,
    ],
)
async def update_rate_limit(db: CurrentSession, pk: int, obj: UpdateRateLimitConfigParam) -> ResponseSchemaModel:
    await rate_limit_service.update(db, pk, obj)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除速率限制配置',
    dependencies=[
        Depends(RequestPermission('llm:rate-limit:del')),
        DependsRBAC,
    ],
)
async def delete_rate_limit(db: CurrentSession, pk: int) -> ResponseSchemaModel:
    await rate_limit_service.delete(db, pk)
    return response_base.success()
