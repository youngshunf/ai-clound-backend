"""阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.content_stage import GetContentStageDetail
from backend.app.hasn_creator.service.content_stage_service import content_stage_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_content_stage',
)
async def get_content_stage(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentStageDetail]]:
    page_data = await content_stage_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播详情',
    name='hasn_creator_open_get_content_stage_detail',
)
async def get_content_stage_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
) -> ResponseSchemaModel[GetContentStageDetail]:
    content_stage = await content_stage_service.get(db=db, pk=pk)
    return response_base.success(data=content_stage)
