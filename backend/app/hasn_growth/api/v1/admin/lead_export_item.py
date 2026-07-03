from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_export_item import (
    CreateLeadExportItemParam,
    DeleteLeadExportItemParam,
    GetLeadExportItemDetail,
    UpdateLeadExportItemParam,
)
from backend.app.hasn_growth.service.lead_export_item_service import lead_export_item_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Lead CSV export item snapshot详情', dependencies=[DependsJwtAuth], name='admin_get_lead_export_item')
async def get_lead_export_item(
    db: CurrentSession, pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')]
) -> ResponseSchemaModel[GetLeadExportItemDetail]:
    lead_export_item = await lead_export_item_service.get(db=db, pk=pk)
    return response_base.success(data=lead_export_item)


@router.get(
    '',
    summary='分页获取所有Lead CSV export item snapshot',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_export_items_paginated')
async def get_lead_export_items_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadExportItemDetail]]:
    page_data = await lead_export_item_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead CSV export item snapshot',
    dependencies=[
        Depends(RequestPermission('lead:export:item:add')),
        DependsRBAC,
    ],
)
async def create_lead_export_item(db: CurrentSessionTransaction, obj: CreateLeadExportItemParam) -> ResponseModel:
    await lead_export_item_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Lead CSV export item snapshot',
    dependencies=[
        Depends(RequestPermission('lead:export:item:edit')),
        DependsRBAC,
    ],
)
async def update_lead_export_item(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')], obj: UpdateLeadExportItemParam
) -> ResponseModel:
    count = await lead_export_item_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Lead CSV export item snapshot',
    dependencies=[
        Depends(RequestPermission('lead:export:item:del')),
        DependsRBAC,
    ],
)
async def delete_lead_export_items(db: CurrentSessionTransaction, obj: DeleteLeadExportItemParam) -> ResponseModel:
    count = await lead_export_item_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
