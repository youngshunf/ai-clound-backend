"""Rejected, invalid, duplicate, or failed lead record - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_rejected_record import GetLeadRejectedRecordDetail
from backend.app.hasn_growth.service.lead_rejected_record_service import lead_rejected_record_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Rejected, invalid, duplicate, or failed lead record列表',
    dependencies=[DependsPagination],
 name='open_get_lead_rejected_records')
async def get_lead_rejected_records(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadRejectedRecordDetail]]:
    page_data = await lead_rejected_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Rejected, invalid, duplicate, or failed lead record详情',
 name='open_get_lead_rejected_record')
async def get_lead_rejected_record(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')],
) -> ResponseSchemaModel[GetLeadRejectedRecordDetail]:
    lead_rejected_record = await lead_rejected_record_service.get(db=db, pk=pk)
    return response_base.success(data=lead_rejected_record)
