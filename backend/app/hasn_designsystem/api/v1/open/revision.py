"""演示文稿版本快照（云端权威历史） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_designsystem.schema.revision import GetRevisionDetail
from backend.app.hasn_designsystem.service.revision_service import revision_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取演示文稿版本快照（云端权威历史）列表',
    dependencies=[DependsPagination],
    name='hasn_designsystem_open_get_revision',
)
async def get_revision(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetRevisionDetail]]:
    page_data = await revision_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取演示文稿版本快照（云端权威历史）详情',
    name='hasn_designsystem_open_get_revision_detail',
)
async def get_revision_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
) -> ResponseSchemaModel[GetRevisionDetail]:
    revision = await revision_service.get(db=db, pk=pk)
    return response_base.success(data=revision)
