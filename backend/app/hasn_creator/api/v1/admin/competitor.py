from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.competitor import (
    CreateCompetitorParam,
    DeleteCompetitorParam,
    GetCompetitorDetail,
    UpdateCompetitorParam,
)
from backend.app.hasn_creator.service.competitor_service import competitor_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取竞品账号（定位/选题调研输入）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_competitor')
async def get_competitor(
    db: CurrentSession, pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')]
) -> ResponseSchemaModel[GetCompetitorDetail]:
    competitor = await competitor_service.get(db=db, pk=pk)
    return response_base.success(data=competitor)


@router.get(
    '',
    summary='分页获取所有竞品账号（定位/选题调研输入）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_competitor_paginated',
)
async def get_competitor_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetCompetitorDetail]]:
    page_data = await competitor_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建竞品账号（定位/选题调研输入）',
    dependencies=[
        Depends(RequestPermission('competitor:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_competitor',
)
async def create_competitor(db: CurrentSessionTransaction, obj: CreateCompetitorParam) -> ResponseModel:
    await competitor_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新竞品账号（定位/选题调研输入）',
    dependencies=[
        Depends(RequestPermission('competitor:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_competitor',
)
async def update_competitor(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')], obj: UpdateCompetitorParam
) -> ResponseModel:
    count = await competitor_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除竞品账号（定位/选题调研输入）',
    dependencies=[
        Depends(RequestPermission('competitor:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_competitor',
)
async def delete_competitor(db: CurrentSessionTransaction, obj: DeleteCompetitorParam) -> ResponseModel:
    count = await competitor_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
