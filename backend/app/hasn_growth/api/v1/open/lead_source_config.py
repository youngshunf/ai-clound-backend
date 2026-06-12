"""AI lead automation source configuration - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_growth.schema.lead_source_config import GetLeadSourceConfigDetail
from backend.app.hasn_growth.service.lead_source_config_service import lead_source_config_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取AI lead automation source configuration列表',
    dependencies=[DependsPagination],
 name='open_get_lead_source_configs')
async def get_lead_source_configs(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadSourceConfigDetail]]:
    page_data = await lead_source_config_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取AI lead automation source configuration详情',
 name='open_get_lead_source_config')
async def get_lead_source_config(
    db: CurrentSession,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')],
) -> ResponseSchemaModel[GetLeadSourceConfigDetail]:
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    return response_base.success(data=lead_source_config)
