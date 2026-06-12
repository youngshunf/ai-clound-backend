"""Lead multi-source evidence - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_contact_source import GetLeadContactSourceDetail
from backend.app.hasn_growth.service.lead_contact_source_service import lead_contact_source_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Lead multi-source evidence列表',
    dependencies=[DependsPagination],
 name='open_get_lead_contact_sources')
async def get_lead_contact_sources(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadContactSourceDetail]]:
    page_data = await lead_contact_source_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Lead multi-source evidence详情',
 name='open_get_lead_contact_source')
async def get_lead_contact_source(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')],
) -> ResponseSchemaModel[GetLeadContactSourceDetail]:
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    return response_base.success(data=lead_contact_source)
