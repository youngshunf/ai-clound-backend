from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    GetMarketplaceTemplateDetail,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='分页获取所有技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_marketplace_template_paginated',
)
async def get_marketplace_template_paginated(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetMarketplaceTemplateDetail]]:
    page_data = await marketplace_template_service.get_list(db=db)
    return response_base.success(data=PageData[GetMarketplaceTemplateDetail].model_validate(page_data))


@router.get(
    '/{resource_id:path}',
    summary='获取技能市场模板详情',
    dependencies=[DependsJwtAuth],
    name='admin_get_marketplace_template',
)
async def get_marketplace_template(
    db: CurrentSession,
    resource_id: Annotated[str, Path(description='模板资源 ID')],
) -> ResponseSchemaModel[GetMarketplaceTemplateDetail]:
    marketplace_template = await marketplace_template_service.get_by_resource_id_admin(
        db=db,
        resource_id=resource_id,
    )
    return response_base.success(data=GetMarketplaceTemplateDetail.model_validate(marketplace_template))


@router.post(
    '',
    summary='创建技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[
        Depends(RequestPermission('marketplace:template:add')),
        DependsRBAC,
    ],
    name='admin_create_marketplace_template',
)
async def create_marketplace_template(
    db: CurrentSessionTransaction,
    obj: CreateMarketplaceTemplateParam,
) -> ResponseSchemaModel[GetMarketplaceTemplateDetail]:
    template = await marketplace_template_service.admin_create(db=db, obj=obj)
    return response_base.success(data=GetMarketplaceTemplateDetail.model_validate(template))


@router.put(
    '/{resource_id:path}',
    summary='更新技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[
        Depends(RequestPermission('marketplace:template:edit')),
        DependsRBAC,
    ],
    name='admin_update_marketplace_template',
)
async def update_marketplace_template(
    db: CurrentSessionTransaction,
    resource_id: Annotated[str, Path(description='模板资源 ID')],
    obj: UpdateMarketplaceTemplateParam,
) -> ResponseSchemaModel[GetMarketplaceTemplateDetail]:
    template = await marketplace_template_service.admin_update(db=db, resource_id=resource_id, obj=obj)
    return response_base.success(data=GetMarketplaceTemplateDetail.model_validate(template))


@router.delete(
    '/{resource_id:path}',
    summary='批量删除技能市场模板表（Agent模板/技能包/SOP包）',
    dependencies=[
        Depends(RequestPermission('marketplace:template:del')),
        DependsRBAC,
    ],
    name='admin_delete_marketplace_template',
)
async def delete_marketplace_template(
    db: CurrentSessionTransaction,
    resource_id: Annotated[str, Path(description='模板资源 ID')],
) -> ResponseModel:
    await marketplace_template_service.admin_delete(db=db, resource_id=resource_id)
    return response_base.success()
