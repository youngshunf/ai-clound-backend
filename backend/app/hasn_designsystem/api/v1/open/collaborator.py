"""设计系统协作分身绑定（对齐 DECKBIND） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_designsystem.schema.collaborator import GetCollaboratorDetail
from backend.app.hasn_designsystem.service.collaborator_service import collaborator_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取设计系统协作分身绑定（对齐 DECKBIND）列表',
    dependencies=[DependsPagination],
    name='hasn_designsystem_open_get_collaborator',
)
async def get_collaborator(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetCollaboratorDetail]]:
    page_data = await collaborator_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取设计系统协作分身绑定（对齐 DECKBIND）详情',
    name='hasn_designsystem_open_get_collaborator_detail',
)
async def get_collaborator_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')],
) -> ResponseSchemaModel[GetCollaboratorDetail]:
    collaborator = await collaborator_service.get(db=db, pk=pk)
    return response_base.success(data=collaborator)
