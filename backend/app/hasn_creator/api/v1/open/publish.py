"""发布记录（= content × account：发到某平台账号 + 数据指标） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.publish import GetPublishDetail
from backend.app.hasn_creator.service.publish_service import publish_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取发布记录（= content × account：发到某平台账号 + 数据指标）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_publish',
)
async def get_publish(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPublishDetail]]:
    page_data = await publish_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取发布记录（= content × account：发到某平台账号 + 数据指标）详情',
    name='hasn_creator_open_get_publish_detail',
)
async def get_publish_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
) -> ResponseSchemaModel[GetPublishDetail]:
    publish = await publish_service.get(db=db, pk=pk)
    return response_base.success(data=publish)
