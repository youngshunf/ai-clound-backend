from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.lead_export_batch import (
    CreateLeadExportBatchParam,
    DeleteLeadExportBatchParam,
    GetLeadExportBatchDetail,
    UpdateLeadExportBatchParam,
)
from backend.app.hasn_growth.service.lead_export_batch_service import lead_export_batch_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Lead CSV export batch详情', dependencies=[DependsJwtAuth], name='admin_get_lead_export_batch')
async def get_lead_export_batch(
    db: CurrentSession, pk: Annotated[int, Path(description='Lead CSV export batch ID')]
) -> ResponseSchemaModel[GetLeadExportBatchDetail]:
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    return response_base.success(data=lead_export_batch)


@router.get(
    '',
    summary='分页获取所有Lead CSV export batch',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_export_batchs_paginated')
async def get_lead_export_batchs_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadExportBatchDetail]]:
    page_data = await lead_export_batch_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead CSV export batch',
    dependencies=[
        Depends(RequestPermission('lead:export:batch:add')),
        DependsRBAC,
    ],
)
async def create_lead_export_batch(db: CurrentSessionTransaction, obj: CreateLeadExportBatchParam) -> ResponseModel:
    await lead_export_batch_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Lead CSV export batch',
    dependencies=[
        Depends(RequestPermission('lead:export:batch:edit')),
        DependsRBAC,
    ],
)
async def update_lead_export_batch(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Lead CSV export batch ID')], obj: UpdateLeadExportBatchParam
) -> ResponseModel:
    count = await lead_export_batch_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Lead CSV export batch',
    dependencies=[
        Depends(RequestPermission('lead:export:batch:del')),
        DependsRBAC,
    ],
)
async def delete_lead_export_batchs(db: CurrentSessionTransaction, obj: DeleteLeadExportBatchParam) -> ResponseModel:
    count = await lead_export_batch_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
