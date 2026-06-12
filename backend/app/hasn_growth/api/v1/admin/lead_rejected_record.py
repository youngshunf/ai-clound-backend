from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.lead_rejected_record import (
    CreateLeadRejectedRecordParam,
    DeleteLeadRejectedRecordParam,
    GetLeadRejectedRecordDetail,
    UpdateLeadRejectedRecordParam,
)
from backend.app.hasn_growth.service.lead_rejected_record_service import lead_rejected_record_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Rejected, invalid, duplicate, or failed lead record详情', dependencies=[DependsJwtAuth], name='admin_get_lead_rejected_record')
async def get_lead_rejected_record(
    db: CurrentSession, pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')]
) -> ResponseSchemaModel[GetLeadRejectedRecordDetail]:
    lead_rejected_record = await lead_rejected_record_service.get(db=db, pk=pk)
    return response_base.success(data=lead_rejected_record)


@router.get(
    '',
    summary='分页获取所有Rejected, invalid, duplicate, or failed lead record',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_rejected_records_paginated')
async def get_lead_rejected_records_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadRejectedRecordDetail]]:
    page_data = await lead_rejected_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Rejected, invalid, duplicate, or failed lead record',
    dependencies=[
        Depends(RequestPermission('lead:rejected:record:add')),
        DependsRBAC,
    ],
)
async def create_lead_rejected_record(db: CurrentSessionTransaction, obj: CreateLeadRejectedRecordParam) -> ResponseModel:
    await lead_rejected_record_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Rejected, invalid, duplicate, or failed lead record',
    dependencies=[
        Depends(RequestPermission('lead:rejected:record:edit')),
        DependsRBAC,
    ],
)
async def update_lead_rejected_record(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')], obj: UpdateLeadRejectedRecordParam
) -> ResponseModel:
    count = await lead_rejected_record_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Rejected, invalid, duplicate, or failed lead record',
    dependencies=[
        Depends(RequestPermission('lead:rejected:record:del')),
        DependsRBAC,
    ],
)
async def delete_lead_rejected_records(db: CurrentSessionTransaction, obj: DeleteLeadRejectedRecordParam) -> ResponseModel:
    count = await lead_rejected_record_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
