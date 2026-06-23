"""视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_studio.schema.studio_asset import GetStudioAssetDetail
from backend.app.hasn_studio.service.studio_asset_service import studio_asset_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）列表',
    dependencies=[DependsPagination],
    name='hasn_studio_open_get_studio_asset',
)
async def get_studio_asset(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioAssetDetail]]:
    page_data = await studio_asset_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）详情',
    name='hasn_studio_open_get_studio_asset_detail',
)
async def get_studio_asset_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
) -> ResponseSchemaModel[GetStudioAssetDetail]:
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    return response_base.success(data=studio_asset)
