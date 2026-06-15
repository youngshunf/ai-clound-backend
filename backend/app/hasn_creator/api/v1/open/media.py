"""素材库；配图/封面/视频/模板（私有桶引用） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.media import GetMediaDetail
from backend.app.hasn_creator.service.media_service import media_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取素材库；配图/封面/视频/模板（私有桶引用）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_media',
)
async def get_media(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetMediaDetail]]:
    page_data = await media_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取素材库；配图/封面/视频/模板（私有桶引用）详情',
    name='hasn_creator_open_get_media_detail',
)
async def get_media_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
) -> ResponseSchemaModel[GetMediaDetail]:
    media = await media_service.get(db=db, pk=pk)
    return response_base.success(data=media)
