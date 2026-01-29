from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.marketplace.schema.marketplace_app_version import (
    CreateMarketplaceAppVersionParam,
    DeleteMarketplaceAppVersionParam,
    GetMarketplaceAppVersionDetail,
    UpdateMarketplaceAppVersionParam,
)
from backend.app.marketplace.service.marketplace_app_version_service import marketplace_app_version_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取应用版本详情', dependencies=[DependsJwtAuth])
async def get_marketplace_app_version(
    db: CurrentSession, pk: Annotated[int, Path(description='应用版本 ID')]
) -> ResponseSchemaModel[GetMarketplaceAppVersionDetail]:
    marketplace_app_version = await marketplace_app_version_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_app_version)


@router.get(
    '',
    summary='分页获取所有应用版本',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_marketplace_app_versions_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceAppVersionDetail]]:
    page_data = await marketplace_app_version_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建应用版本',
    dependencies=[
        Depends(RequestPermission('marketplace:app:version:add')),
        DependsRBAC,
    ],
)
async def create_marketplace_app_version(db: CurrentSessionTransaction, obj: CreateMarketplaceAppVersionParam) -> ResponseModel:
    await marketplace_app_version_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新应用版本',
    dependencies=[
        Depends(RequestPermission('marketplace:app:version:edit')),
        DependsRBAC,
    ],
)
async def update_marketplace_app_version(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='应用版本 ID')], obj: UpdateMarketplaceAppVersionParam
) -> ResponseModel:
    count = await marketplace_app_version_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除应用版本',
    dependencies=[
        Depends(RequestPermission('marketplace:app:version:del')),
        DependsRBAC,
    ],
)
async def delete_marketplace_app_versions(db: CurrentSessionTransaction, obj: DeleteMarketplaceAppVersionParam) -> ResponseModel:
    count = await marketplace_app_version_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
