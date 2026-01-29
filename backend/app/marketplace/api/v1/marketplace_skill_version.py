from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.marketplace.schema.marketplace_skill_version import (
    CreateMarketplaceSkillVersionParam,
    DeleteMarketplaceSkillVersionParam,
    GetMarketplaceSkillVersionDetail,
    UpdateMarketplaceSkillVersionParam,
)
from backend.app.marketplace.service.marketplace_skill_version_service import marketplace_skill_version_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取技能版本详情', dependencies=[DependsJwtAuth])
async def get_marketplace_skill_version(
    db: CurrentSession, pk: Annotated[int, Path(description='技能版本 ID')]
) -> ResponseSchemaModel[GetMarketplaceSkillVersionDetail]:
    marketplace_skill_version = await marketplace_skill_version_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_skill_version)


@router.get(
    '',
    summary='分页获取所有技能版本',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_marketplace_skill_versions_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceSkillVersionDetail]]:
    page_data = await marketplace_skill_version_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建技能版本',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:version:add')),
        DependsRBAC,
    ],
)
async def create_marketplace_skill_version(db: CurrentSessionTransaction, obj: CreateMarketplaceSkillVersionParam) -> ResponseModel:
    await marketplace_skill_version_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新技能版本',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:version:edit')),
        DependsRBAC,
    ],
)
async def update_marketplace_skill_version(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='技能版本 ID')], obj: UpdateMarketplaceSkillVersionParam
) -> ResponseModel:
    count = await marketplace_skill_version_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除技能版本',
    dependencies=[
        Depends(RequestPermission('marketplace:skill:version:del')),
        DependsRBAC,
    ],
)
async def delete_marketplace_skill_versions(db: CurrentSessionTransaction, obj: DeleteMarketplaceSkillVersionParam) -> ResponseModel:
    count = await marketplace_skill_version_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
