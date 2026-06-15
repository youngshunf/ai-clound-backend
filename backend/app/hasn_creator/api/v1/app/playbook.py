"""获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.playbook import (
    CreatePlaybookParam,
    GetPlaybookDetail,
    UpdatePlaybookParam,
)
from backend.app.hasn_creator.service.playbook_service import playbook_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_playbook',
)
async def get_my_playbook(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPlaybookDetail]]:
    page_data = await playbook_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_playbook',
)
async def create_my_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreatePlaybookParam,
) -> ResponseModel:
    result = await playbook_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_playbook_detail',
)
async def get_my_playbook_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
) -> ResponseSchemaModel[GetPlaybookDetail]:
    playbook = await playbook_service.get(db=db, pk=pk)
    if playbook.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    return response_base.success(data=playbook)


@router.put(
    '/{pk}',
    summary='更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_playbook',
)
async def update_my_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
    obj: UpdatePlaybookParam,
) -> ResponseModel:
    playbook = await playbook_service.get(db=db, pk=pk)
    if getattr(playbook, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    count = await playbook_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_playbook',
)
async def delete_my_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID')],
) -> ResponseModel:
    user_id = request.user.id
    playbook = await playbook_service.get(db=db, pk=pk)
    if playbook.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义')
    from backend.app.hasn_creator.schema.playbook import DeletePlaybookParam
    count = await playbook_service.delete(db=db, obj=DeletePlaybookParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
