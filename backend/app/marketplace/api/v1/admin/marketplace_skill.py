from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.marketplace.schema.marketplace_skill import (
    CreateMarketplaceSkillParam,
    GetMarketplaceSkillDetail,
    UpdateMarketplaceSkillParam,
)
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='分页获取所有技能市场技能',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_marketplace_skill_paginated',
)
async def get_marketplace_skill_paginated(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetMarketplaceSkillDetail]]:
    page_data = await marketplace_skill_service.get_list(db=db)
    return response_base.success(data=PageData[GetMarketplaceSkillDetail].model_validate(page_data))


@router.get(
    '/{resource_id:path}',
    summary='获取技能市场技能详情',
    dependencies=[DependsJwtAuth],
    name='admin_get_marketplace_skill',
)
async def get_marketplace_skill(
    db: CurrentSession,
    resource_id: Annotated[str, Path(description='技能资源 ID')],
) -> ResponseSchemaModel[GetMarketplaceSkillDetail]:
    marketplace_skill = await marketplace_skill_service.get_by_resource_id_admin(db=db, resource_id=resource_id)
    return response_base.success(data=GetMarketplaceSkillDetail.model_validate(marketplace_skill))


@router.post(
    '',
    summary='创建技能市场技能',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:add')),
        DependsRBAC,
    ],
    name='admin_create_marketplace_skill',
)
async def create_marketplace_skill(
    db: CurrentSessionTransaction,
    obj: CreateMarketplaceSkillParam,
) -> ResponseSchemaModel[GetMarketplaceSkillDetail]:
    skill = await marketplace_skill_service.admin_create(db=db, obj=obj)
    return response_base.success(data=GetMarketplaceSkillDetail.model_validate(skill))


@router.put(
    '/{resource_id:path}',
    summary='更新技能市场技能',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:edit')),
        DependsRBAC,
    ],
    name='admin_update_marketplace_skill',
)
async def update_marketplace_skill(
    db: CurrentSessionTransaction,
    resource_id: Annotated[str, Path(description='技能资源 ID')],
    obj: UpdateMarketplaceSkillParam,
) -> ResponseSchemaModel[GetMarketplaceSkillDetail]:
    skill = await marketplace_skill_service.admin_update(db=db, resource_id=resource_id, obj=obj)
    return response_base.success(data=GetMarketplaceSkillDetail.model_validate(skill))


@router.delete(
    '/{resource_id:path}',
    summary='批量删除技能市场技能',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:del')),
        DependsRBAC,
    ],
    name='admin_delete_marketplace_skill',
)
async def delete_marketplace_skill(
    db: CurrentSessionTransaction,
    resource_id: Annotated[str, Path(description='技能资源 ID')],
) -> ResponseModel:
    await marketplace_skill_service.admin_delete(db=db, resource_id=resource_id)
    return response_base.success()
