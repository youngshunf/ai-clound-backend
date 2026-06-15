"""草稿箱（灵感快速捕获，轻量独立于正式流水线） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.draft import GetDraftDetail
from backend.app.hasn_creator.service.draft_service import draft_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取草稿箱（灵感快速捕获，轻量独立于正式流水线）列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_draft',
)
async def get_draft(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetDraftDetail]]:
    page_data = await draft_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取草稿箱（灵感快速捕获，轻量独立于正式流水线）详情',
    name='hasn_creator_open_get_draft_detail',
)
async def get_draft_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
) -> ResponseSchemaModel[GetDraftDetail]:
    draft = await draft_service.get(db=db, pk=pk)
    return response_base.success(data=draft)
