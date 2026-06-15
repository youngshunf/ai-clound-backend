"""内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.content import GetContentDetail
from backend.app.hasn_creator.service.content_service import content_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_content',
)
async def get_content(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentDetail]]:
    page_data = await content_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核详情',
    name='hasn_creator_open_get_content_detail',
)
async def get_content_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
) -> ResponseSchemaModel[GetContentDetail]:
    content = await content_service.get(db=db, pk=pk)
    return response_base.success(data=content)
