"""一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_reel.schema.reel_creation import GetReelCreationDetail
from backend.app.hasn_reel.service.reel_creation_service import reel_creation_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）列表',
    dependencies=[DependsPagination],
    name='hasn_reel_open_get_reel_creation',
)
async def get_reel_creation(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetReelCreationDetail]]:
    page_data = await reel_creation_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）详情',
    name='hasn_reel_open_get_reel_creation_detail',
)
async def get_reel_creation_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
) -> ResponseSchemaModel[GetReelCreationDetail]:
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    return response_base.success(data=reel_creation)
