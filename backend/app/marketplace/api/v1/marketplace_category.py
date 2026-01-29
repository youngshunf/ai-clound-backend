from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.marketplace.schema.marketplace_category import (
    CreateMarketplaceCategoryParam,
    DeleteMarketplaceCategoryParam,
    GetMarketplaceCategoryDetail,
    UpdateMarketplaceCategoryParam,
)
from backend.app.marketplace.service.marketplace_category_service import marketplace_category_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取技能市场分类详情', dependencies=[DependsJwtAuth])
async def get_marketplace_category(
    db: CurrentSession, pk: Annotated[int, Path(description='技能市场分类 ID')]
) -> ResponseSchemaModel[GetMarketplaceCategoryDetail]:
    marketplace_category = await marketplace_category_service.get(db=db, pk=pk)
    return response_base.success(data=marketplace_category)


@router.get(
    '',
    summary='分页获取所有技能市场分类',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
)
async def get_marketplace_categorys_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMarketplaceCategoryDetail]]:
    page_data = await marketplace_category_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建技能市场分类',
    dependencies=[
        Depends(RequestPermission('marketplace:category:add')),
        DependsRBAC,
    ],
)
async def create_marketplace_category(db: CurrentSessionTransaction, obj: CreateMarketplaceCategoryParam) -> ResponseModel:
    await marketplace_category_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新技能市场分类',
    dependencies=[
        Depends(RequestPermission('marketplace:category:edit')),
        DependsRBAC,
    ],
)
async def update_marketplace_category(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='技能市场分类 ID')], obj: UpdateMarketplaceCategoryParam
) -> ResponseModel:
    count = await marketplace_category_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除技能市场分类',
    dependencies=[
        Depends(RequestPermission('marketplace:category:del')),
        DependsRBAC,
    ],
)
async def delete_marketplace_categorys(db: CurrentSessionTransaction, obj: DeleteMarketplaceCategoryParam) -> ResponseModel:
    count = await marketplace_category_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
