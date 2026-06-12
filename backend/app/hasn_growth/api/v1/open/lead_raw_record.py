"""Raw crawled lead page record - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_raw_record import GetLeadRawRecordDetail
from backend.app.hasn_growth.service.lead_raw_record_service import lead_raw_record_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Raw crawled lead page record列表',
    dependencies=[DependsPagination],
 name='open_get_lead_raw_records')
async def get_lead_raw_records(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadRawRecordDetail]]:
    page_data = await lead_raw_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Raw crawled lead page record详情',
 name='open_get_lead_raw_record')
async def get_lead_raw_record(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Raw crawled lead page record ID')],
) -> ResponseSchemaModel[GetLeadRawRecordDetail]:
    lead_raw_record = await lead_raw_record_service.get(db=db, pk=pk)
    return response_base.success(data=lead_raw_record)
