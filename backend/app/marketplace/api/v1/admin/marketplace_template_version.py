from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    DeleteMarketplaceTemplateVersionParam,
    GetMarketplaceTemplateVersionDetail,
    UpdateMarketplaceTemplateVersionParam,
)
from backend.app.marketplace.service.marketplace_template_version_service import marketplace_template_version_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取模板版本详情', dependencies=[DependsJwtAuth], name='admin_get_marketplace_template_version')
async def get_marketplace_template_version(
    db: CurrentSession, pk: Annotated[int, Path(description='模板版本 ID')]
) -> ResponseSchemaModel[GetMarketplaceTemplateVersionDetail]:
    marketplace_template_version = await marketplace_template_version_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_template_version)


@router.get(
    '',
    summary='分页获取所有模板版本',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_marketplace_template_version_paginated',
)
async def get_marketplace_template_version_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceTemplateVersionDetail]]:
    page_data = await marketplace_template_version_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建模板版本',
    dependencies=[
        Depends(RequestPermission('marketplace:template:version:add')),
        DependsRBAC,
    ],
    name='admin_create_marketplace_template_version',
)
async def create_marketplace_template_version(db: CurrentSessionTransaction, obj: CreateMarketplaceTemplateVersionParam) -> ResponseModel:
    await marketplace_template_version_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新模板版本',
    dependencies=[
        Depends(RequestPermission('marketplace:template:version:edit')),
        DependsRBAC,
    ],
    name='admin_update_marketplace_template_version',
)
async def update_marketplace_template_version(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='模板版本 ID')], obj: UpdateMarketplaceTemplateVersionParam
) -> ResponseModel:
    count = await marketplace_template_version_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除模板版本',
    dependencies=[
        Depends(RequestPermission('marketplace:template:version:del')),
        DependsRBAC,
    ],
    name='admin_delete_marketplace_template_version',
)
async def delete_marketplace_template_version(db: CurrentSessionTransaction, obj: DeleteMarketplaceTemplateVersionParam) -> ResponseModel:
    count = await marketplace_template_version_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
