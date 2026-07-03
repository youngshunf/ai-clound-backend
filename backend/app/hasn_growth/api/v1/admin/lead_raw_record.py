from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_raw_record import (
    CreateLeadRawRecordParam,
    DeleteLeadRawRecordParam,
    GetLeadRawRecordDetail,
    UpdateLeadRawRecordParam,
)
from backend.app.hasn_growth.service.lead_raw_record_service import lead_raw_record_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Raw crawled lead page record详情', dependencies=[DependsJwtAuth], name='admin_get_lead_raw_record')
async def get_lead_raw_record(
    db: CurrentSession, pk: Annotated[int, Path(description='Raw crawled lead page record ID')]
) -> ResponseSchemaModel[GetLeadRawRecordDetail]:
    lead_raw_record = await lead_raw_record_service.get(db=db, pk=pk)
    return response_base.success(data=lead_raw_record)


@router.get(
    '',
    summary='分页获取所有Raw crawled lead page record',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_raw_records_paginated')
async def get_lead_raw_records_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadRawRecordDetail]]:
    page_data = await lead_raw_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Raw crawled lead page record',
    dependencies=[
        Depends(RequestPermission('lead:raw:record:add')),
        DependsRBAC,
    ],
)
async def create_lead_raw_record(db: CurrentSessionTransaction, obj: CreateLeadRawRecordParam) -> ResponseModel:
    await lead_raw_record_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Raw crawled lead page record',
    dependencies=[
        Depends(RequestPermission('lead:raw:record:edit')),
        DependsRBAC,
    ],
)
async def update_lead_raw_record(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Raw crawled lead page record ID')], obj: UpdateLeadRawRecordParam
) -> ResponseModel:
    count = await lead_raw_record_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Raw crawled lead page record',
    dependencies=[
        Depends(RequestPermission('lead:raw:record:del')),
        DependsRBAC,
    ],
)
async def delete_lead_raw_records(db: CurrentSessionTransaction, obj: DeleteLeadRawRecordParam) -> ResponseModel:
    count = await lead_raw_record_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
