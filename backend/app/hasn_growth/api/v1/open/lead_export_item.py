"""Lead CSV export item snapshot - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_export_item import GetLeadExportItemDetail
from backend.app.hasn_growth.service.lead_export_item_service import lead_export_item_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Lead CSV export item snapshot列表',
    dependencies=[DependsPagination],
 name='open_get_lead_export_items')
async def get_lead_export_items(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadExportItemDetail]]:
    page_data = await lead_export_item_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Lead CSV export item snapshot详情',
 name='open_get_lead_export_item')
async def get_lead_export_item(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')],
) -> ResponseSchemaModel[GetLeadExportItemDetail]:
    lead_export_item = await lead_export_item_service.get(db=db, pk=pk)
    return response_base.success(data=lead_export_item)
