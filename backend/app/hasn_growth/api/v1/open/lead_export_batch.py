"""Lead CSV export batch - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_export_batch import GetLeadExportBatchDetail
from backend.app.hasn_growth.service.lead_export_batch_service import lead_export_batch_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Lead CSV export batch列表',
    dependencies=[DependsPagination],
 name='open_get_lead_export_batchs')
async def get_lead_export_batchs(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadExportBatchDetail]]:
    page_data = await lead_export_batch_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Lead CSV export batch详情',
 name='open_get_lead_export_batch')
async def get_lead_export_batch(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')],
) -> ResponseSchemaModel[GetLeadExportBatchDetail]:
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    return response_base.success(data=lead_export_batch)
