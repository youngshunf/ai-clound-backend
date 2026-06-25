from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.playbook import (
    CreatePlaybookParam,
    DeletePlaybookParam,
    GetPlaybookDetail,
    UpdatePlaybookParam,
)
from backend.app.hasn_creator.service.playbook_service import playbook_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_playbook')
async def get_playbook(
    db: CurrentSession, pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')]
) -> ResponseSchemaModel[GetPlaybookDetail]:
    playbook = await playbook_service.get(db=db, pk=pk)
    return response_base.success(data=playbook)


@router.get(
    '',
    summary='分页获取所有获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_playbook_paginated',
)
async def get_playbook_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetPlaybookDetail]]:
    page_data = await playbook_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[
        Depends(RequestPermission('playbook:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_playbook',
)
async def create_playbook(db: CurrentSessionTransaction, obj: CreatePlaybookParam) -> ResponseModel:
    await playbook_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[
        Depends(RequestPermission('playbook:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_playbook',
)
async def update_playbook(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')], obj: UpdatePlaybookParam
) -> ResponseModel:
    count = await playbook_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[
        Depends(RequestPermission('playbook:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_playbook',
)
async def delete_playbook(db: CurrentSessionTransaction, obj: DeletePlaybookParam) -> ResponseModel:
    count = await playbook_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
