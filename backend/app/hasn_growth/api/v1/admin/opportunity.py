from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.opportunity import (
    CreateOpportunityParam,
    DeleteOpportunityParam,
    GetOpportunityDetail,
    UpdateOpportunityParam,
)
from backend.app.hasn_growth.service.opportunity_service import opportunity_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客商机（阶段推进 + 金额 + 成交/败因登记）详情', dependencies=[DependsJwtAuth], name='admin_get_opportunity')
async def get_opportunity(
    db: CurrentSession, pk: Annotated[int, Path(description='获客商机（阶段推进 + 金额 + 成交/败因登记） ID')]
) -> ResponseSchemaModel[GetOpportunityDetail]:
    opportunity = await opportunity_service.get(db=db, pk=pk)
    return response_base.success(data=opportunity)


@router.get(
    '',
    summary='分页获取所有获客商机（阶段推进 + 金额 + 成交/败因登记）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_opportunity_paginated',
)
async def get_opportunity_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetOpportunityDetail]]:
    page_data = await opportunity_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客商机（阶段推进 + 金额 + 成交/败因登记）',
    dependencies=[
        Depends(RequestPermission('opportunity:add')),
        DependsRBAC,
    ],
    name='admin_create_opportunity',
)
async def create_opportunity(db: CurrentSessionTransaction, obj: CreateOpportunityParam) -> ResponseModel:
    await opportunity_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客商机（阶段推进 + 金额 + 成交/败因登记）',
    dependencies=[
        Depends(RequestPermission('opportunity:edit')),
        DependsRBAC,
    ],
    name='admin_update_opportunity',
)
async def update_opportunity(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客商机（阶段推进 + 金额 + 成交/败因登记） ID')], obj: UpdateOpportunityParam
) -> ResponseModel:
    count = await opportunity_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客商机（阶段推进 + 金额 + 成交/败因登记）',
    dependencies=[
        Depends(RequestPermission('opportunity:del')),
        DependsRBAC,
    ],
    name='admin_delete_opportunity',
)
async def delete_opportunity(db: CurrentSessionTransaction, obj: DeleteOpportunityParam) -> ResponseModel:
    count = await opportunity_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
