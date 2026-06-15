"""获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.playbook import GetPlaybookDetail
from backend.app.hasn_creator.service.playbook_service import playbook_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_playbook',
)
async def get_playbook(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPlaybookDetail]]:
    page_data = await playbook_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义详情',
    name='hasn_creator_open_get_playbook_detail',
)
async def get_playbook_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
) -> ResponseSchemaModel[GetPlaybookDetail]:
    playbook = await playbook_service.get(db=db, pk=pk)
    return response_base.success(data=playbook)
