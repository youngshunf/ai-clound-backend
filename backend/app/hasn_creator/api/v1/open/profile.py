"""项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.profile import GetProfileDetail
from backend.app.hasn_creator.service.profile_service import profile_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_profile',
)
async def get_profile(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetProfileDetail]]:
    page_data = await profile_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）详情',
    name='hasn_creator_open_get_profile_detail',
)
async def get_profile_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
) -> ResponseSchemaModel[GetProfileDetail]:
    profile = await profile_service.get(db=db, pk=pk)
    return response_base.success(data=profile)
