"""Firecrawl request audit for AI lead automation - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_firecrawl_request import GetLeadFirecrawlRequestDetail
from backend.app.hasn_growth.service.lead_firecrawl_request_service import lead_firecrawl_request_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取Firecrawl request audit for AI lead automation列表',
    dependencies=[DependsPagination],
 name='open_get_lead_firecrawl_requests')
async def get_lead_firecrawl_requests(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadFirecrawlRequestDetail]]:
    page_data = await lead_firecrawl_request_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取Firecrawl request audit for AI lead automation详情',
 name='open_get_lead_firecrawl_request')
async def get_lead_firecrawl_request(
    db: CurrentSession,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')],
) -> ResponseSchemaModel[GetLeadFirecrawlRequestDetail]:
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    return response_base.success(data=lead_firecrawl_request)
