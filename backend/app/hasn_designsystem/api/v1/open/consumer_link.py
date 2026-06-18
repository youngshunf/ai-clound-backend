"""设计系统下游消费登记（换系统重渲染追踪） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_designsystem.schema.consumer_link import GetConsumerLinkDetail
from backend.app.hasn_designsystem.service.consumer_link_service import consumer_link_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取设计系统下游消费登记（换系统重渲染追踪）列表',
    dependencies=[DependsPagination],
    name='hasn_designsystem_open_get_consumer_link',
)
async def get_consumer_link(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetConsumerLinkDetail]]:
    page_data = await consumer_link_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取设计系统下游消费登记（换系统重渲染追踪）详情',
    name='hasn_designsystem_open_get_consumer_link_detail',
)
async def get_consumer_link_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统下游消费登记（换系统重渲染追踪） ID')],
) -> ResponseSchemaModel[GetConsumerLinkDetail]:
    consumer_link = await consumer_link_service.get(db=db, pk=pk)
    return response_base.success(data=consumer_link)
