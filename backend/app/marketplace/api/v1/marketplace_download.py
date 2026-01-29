from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.marketplace.schema.marketplace_download import (
    CreateMarketplaceDownloadParam,
    DeleteMarketplaceDownloadParam,
    GetMarketplaceDownloadDetail,
    UpdateMarketplaceDownloadParam,
)
from backend.app.marketplace.service.marketplace_download_service import marketplace_download_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取用户下载记录详情', dependencies=[DependsJwtAuth])
async def get_marketplace_download(
    db: CurrentSession, pk: Annotated[int, Path(description='用户下载记录 ID')]
) -> ResponseSchemaModel[GetMarketplaceDownloadDetail]:
    marketplace_download = await marketplace_download_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_download)


@router.get(
    '',
    summary='分页获取所有用户下载记录',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_marketplace_downloads_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceDownloadDetail]]:
    page_data = await marketplace_download_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建用户下载记录',
    dependencies=[
        Depends(RequestPermission('marketplace:download:add')),
        DependsRBAC,
    ],
)
async def create_marketplace_download(db: CurrentSessionTransaction, obj: CreateMarketplaceDownloadParam) -> ResponseModel:
    await marketplace_download_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新用户下载记录',
    dependencies=[
        Depends(RequestPermission('marketplace:download:edit')),
        DependsRBAC,
    ],
)
async def update_marketplace_download(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='用户下载记录 ID')], obj: UpdateMarketplaceDownloadParam
) -> ResponseModel:
    count = await marketplace_download_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除用户下载记录',
    dependencies=[
        Depends(RequestPermission('marketplace:download:del')),
        DependsRBAC,
    ],
)
async def delete_marketplace_downloads(db: CurrentSessionTransaction, obj: DeleteMarketplaceDownloadParam) -> ResponseModel:
    count = await marketplace_download_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
